import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from folder_service import repository, retention_actions
from folder_service.approval_client import ApprovalClient
from folder_service.consumer import start_consuming
from folder_service.document_client import DocumentClient
from folder_service.models import Base, Folder
from folder_service.object_type_client import ObjectTypeClient
from folder_service.schemas import (
    DeletionRegisterEntryOut,
    FolderCreate,
    FolderOut,
    FolderUpdate,
    LegalHoldCreate,
    LegalHoldOut,
    LegalHoldReleaseRequest,
    RetentionConfigIn,
    RetentionConfigOut,
    RetentionUpdate,
    TrashConfigIn,
    TrashConfigOut,
    TrashRequest,
    TrashResult,
)
from folder_service.settings import ROOT_FOLDER_ID, Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def _resolve_deletion_reason_required(
    session: AsyncSession, object_type_id: int | None
) -> bool:
    """Löschgrund-Pflicht (5.2a) - installationsweiter Default aus
    `RetentionConfig`, je Objekttyp per `deletion_reason_required_override`
    überschreibbar. Identisches Muster wie
    `document_service.main._resolve_deletion_reason_required` (P7-S1)."""
    config = await repository.get_retention_config(session)
    if object_type_id is not None:
        object_type = await app.state.object_type_client.get(object_type_id)
        if (
            object_type is not None
            and object_type.get("deletion_reason_required_override") is not None
        ):
            return object_type["deletion_reason_required_override"]
    return config.deletion_reason_required


async def _execute_or_defer_forced_deletion(session: AsyncSession, folder: Folder) -> None:
    """Physische Ordner-Zwangslöschung (5.2a, seit P7-S1b) - anders als bei
    Dokumenten (P7-S1) wird hier zusätzlich geprüft, ob der Teilbaum noch
    aktive Unterordner/Dokumente enthält: ist das der Fall, wird die
    Zwangslöschung für diesen Tick übersprungen (kein automatisches
    Mit-Zwangslöschen enthaltener Objekte, siehe docs/services/
    folder-service.md "Offene Punkte"). Ansonsten identisches Vier-Augen-
    Muster wie `document_service.main._execute_or_defer_forced_deletion`."""
    folder_id = folder.id
    subtree_ids = await repository.list_active_subtree_ids(session, folder_id)
    if (
        await repository.has_any_child_folder_row(session, folder_id)
        or await app.state.document_client.count_active(subtree_ids) > 0
    ):
        logger.info(
            "Zwangslöschung für folder_id=%r übersprungen - Teilbaum nicht leer, "
            "wird beim nächsten Tick erneut versucht",
            folder_id,
        )
        return

    reason = folder.pending_deletion_reason
    if await app.state.approval_client.requires_approval("folder.force_delete"):
        if folder.force_delete_approval_requested_at is not None:
            return
        await app.state.approval_client.create_request(
            action_type="folder.force_delete",
            initiated_by="system:retention-poll",
            payload={
                "folder_id": folder_id,
                "reason": reason,
                "triggered_by": "system:retention-poll",
            },
        )
        folder.force_delete_approval_requested_at = datetime.now(UTC)
        await session.commit()
        return

    await retention_actions.execute_forced_deletion(
        session, folder_id, reason=reason, triggered_by="system:retention-poll"
    )
    await session.commit()
    await publish_event(
        "folder.force_deleted",
        folder_id,
        {"reason": reason, "triggered_by": "system:retention-poll"},
        actor="system:retention-poll",
    )


async def _retention_poll_loop(session_factory) -> None:
    """Aufbewahrung/Legal Hold/Zwangslöschung für Ordner (5.2/5.2a, seit
    P7-S1b) - identisches Poll-Loop-Idiom wie `document_service.main.
    _retention_poll_loop` (P7-S1, seinerseits ADR 0020)."""
    while True:
        try:
            async with session_factory() as session:
                config = await repository.get_retention_config(session)
                if config.reminder_lead_days is not None:
                    for folder in await repository.list_due_for_reminder(
                        session, lead_days=config.reminder_lead_days
                    ):
                        folder.deletion_reminder_sent_at = datetime.now(UTC)
                        await session.flush()
                        await session.commit()
                        await publish_event(
                            "folder.deletion.reminder",
                            folder.id,
                            {
                                "name": folder.name,
                                "retention_until": folder.retention_until.isoformat()
                                if folder.retention_until
                                else None,
                                "full_deletion": folder.full_deletion,
                                "notify_email": folder.reminder_notify_email,
                            },
                            actor="system:retention-poll",
                        )

            async with session_factory() as session:
                for folder in await repository.list_due_for_retention_action(session):
                    if folder.full_deletion:
                        await _execute_or_defer_forced_deletion(session, folder)
                        continue
                    folder_id = folder.id
                    await repository.soft_delete_folder(
                        session,
                        folder_id,
                        deleted_by="system:retention-poll",
                        document_client=app.state.document_client,
                    )
                    await session.commit()
                    await publish_event(
                        "folder.trashed",
                        folder_id,
                        {"deleted_by": "system:retention-poll"},
                        actor="system:retention-poll",
                    )

            async with session_factory() as session:
                trash_config = await repository.get_trash_config(session)
                for folder in await repository.list_expired_trash(
                    session, restore_period_days=trash_config.restore_period_days
                ):
                    folder_id = folder.id
                    await retention_actions.purge_expired_trash_entry(session, folder_id)
                    await session.commit()
                    await publish_event(
                        "folder.trash_purged",
                        folder_id,
                        {"trigger": "trash_expiry"},
                        actor="system:retention-poll",
                    )
        except Exception:
            logger.exception(
                "Retention-Poll-Tick fehlgeschlagen - wird beim nächsten Tick erneut versucht."
            )
        await asyncio.sleep(settings.retention_poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS folder"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc-Schema-Erweiterung (kein Alembic in dieser frühen Phase, siehe
        # CONTRIBUTING.md) - Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a,
        # seit P7-S1b), gleiches Muster wie document-service (P7-S1).
        await conn.execute(
            text("ALTER TABLE folder.folder ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ")
        )
        await conn.execute(
            text(
                "ALTER TABLE folder.folder "
                "ADD COLUMN IF NOT EXISTS deleted_via_folder_id VARCHAR(128)"
            )
        )
        await conn.execute(
            text("ALTER TABLE folder.folder ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ")
        )
        await conn.execute(
            text(
                "ALTER TABLE folder.folder "
                "ADD COLUMN IF NOT EXISTS full_deletion BOOLEAN DEFAULT FALSE NOT NULL"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE folder.folder "
                "ADD COLUMN IF NOT EXISTS pending_deletion_reason VARCHAR(1024)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE folder.folder "
                "ADD COLUMN IF NOT EXISTS deletion_reminder_sent_at TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE folder.folder "
                "ADD COLUMN IF NOT EXISTS reminder_notify_email VARCHAR(255)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE folder.folder "
                "ADD COLUMN IF NOT EXISTS force_delete_approval_requested_at TIMESTAMPTZ"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    async with app.state.session_factory() as session:
        await repository.ensure_root_folder(session)
        # Singleton-Configs einmalig vor dem ersten Request/Poll-Tick anlegen
        # (Race-Vermeidung, siehe document-service P7-S1).
        await repository.get_retention_config(session)
        await repository.get_trash_config(session)
        await session.commit()

    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)
    app.state.document_client = DocumentClient(settings.document_service_base_url)
    app.state.approval_client = ApprovalClient(settings.permission_service_base_url)

    event_bus = NatsEventBusClient(settings.nats_url, stream="folder")
    await event_bus.connect()
    app.state.event_bus = event_bus

    # Erster Konsument dieses Service überhaupt (5.2a, seit P7-S1b) - eigener
    # Client (ensure_stream=False), da folder-service den Stream "permission"
    # nicht selbst besitzt (gleiches Zwei-Client-Prinzip wie document-service).
    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    await start_consuming(
        consumer_bus,
        settings.subjects,
        app.state.session_factory,
        publish_event,
        app.state.document_client,
    )

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    retention_poll_task = asyncio.create_task(_retention_poll_loop(app.state.session_factory))

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    retention_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await retention_poll_task
    if registration:
        await registration.stop()
    await consumer_bus.close()
    await event_bus.close()
    await app.state.object_type_client.close()
    await app.state.document_client.close()
    await app.state.approval_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(
    event_type: str, subject: str, payload: dict, actor: str | None = None
) -> None:
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
        actor=actor,
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


async def _validate_against_object_type(
    object_type_id: int | None,
    *,
    name: str,
    attributes: dict,
    parent_object_type_id: int | None,
    parent_is_root: bool,
):
    if object_type_id is None:
        return
    errors = await app.state.object_type_client.validate(
        object_type_id,
        name=name,
        attributes=attributes,
        parent_object_type_id=parent_object_type_id,
        parent_is_root=parent_is_root,
    )
    if errors:
        raise HTTPException(status_code=400, detail={"errors": errors})


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/folders", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: FolderCreate, session: AsyncSession = Depends(get_session)
) -> FolderOut:
    try:
        parent_folder = await repository.get_folder(session, payload.parent_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await _validate_against_object_type(
        payload.object_type_id,
        name=payload.name,
        attributes=payload.attributes,
        parent_object_type_id=parent_folder.object_type_id,
        parent_is_root=payload.parent_id == ROOT_FOLDER_ID,
    )
    try:
        folder = await repository.create_folder(
            session,
            name=payload.name,
            parent_id=payload.parent_id,
            object_type_id=payload.object_type_id,
            attributes=payload.attributes,
            created_by=payload.created_by,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Aufbewahrung (5.2, seit P7-S1): Typ-Default einmalig in ein konkretes
    # Datum übersetzt, identisches Muster wie document-service. Gilt bereits
    # seit P7-S1 objektartübergreifend für `applies_to="folder"`.
    if payload.object_type_id is not None:
        object_type = await app.state.object_type_client.get(payload.object_type_id)
        if object_type and object_type.get("default_retention_days") is not None:
            folder.retention_until = datetime.now(UTC) + timedelta(
                days=object_type["default_retention_days"]
            )
            await session.flush()

    await session.commit()
    await publish_event(
        "folder.resource.created",
        subject=folder.id,
        payload={
            "resource_id": folder.id,
            "parent_id": folder.parent_id,
            "resource_type": "folder",
        },
        actor=payload.created_by,
    )
    return folder


@app.get("/folders/deleted", response_model=list[FolderOut])
async def list_deleted_folders(
    parent_id: str, session: AsyncSession = Depends(get_session)
) -> list[FolderOut]:
    """Papierkorb-Inhalt eines Ordners (5.2, seit P7-S1b) - Route MUSS vor
    `/folders/{folder_id}` registriert sein, sonst würde FastAPI "deleted"
    fälschlich als `folder_id` interpretieren (gleiche Falle wie bei
    document-service, siehe main.py dort)."""
    return await repository.list_deleted_folders(session, parent_id)


@app.get("/folders/{folder_id}", response_model=FolderOut)
async def get_folder(folder_id: str, session: AsyncSession = Depends(get_session)) -> FolderOut:
    try:
        return await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/folders/{folder_id}/children", response_model=list[FolderOut])
async def list_children(
    folder_id: str, session: AsyncSession = Depends(get_session)
) -> list[FolderOut]:
    try:
        return await repository.list_children(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/folders/{folder_id}", response_model=FolderOut)
async def update_folder(
    folder_id: str, payload: FolderUpdate, session: AsyncSession = Depends(get_session)
) -> FolderOut:
    try:
        current = await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    is_move = payload.parent_id is not None and payload.parent_id != current.parent_id
    if is_move and current.object_type_id is not None:
        try:
            new_parent = await repository.get_folder(session, payload.parent_id)
        except repository.NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await _validate_against_object_type(
            current.object_type_id,
            name=payload.name if payload.name is not None else current.name,
            attributes=payload.attributes if payload.attributes is not None else current.attributes,
            parent_object_type_id=new_parent.object_type_id,
            parent_is_root=payload.parent_id == ROOT_FOLDER_ID,
        )

    try:
        folder, moved = await repository.update_folder(
            session,
            folder_id,
            name=payload.name,
            new_parent_id=payload.parent_id,
            attributes=payload.attributes,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    if moved:
        # Kein Akteur bekannt - FolderUpdate verfolgt bislang nicht, wer die
        # Verschiebung/Umbenennung ausgeloest hat (P7-S2: nur bereits
        # vorhandene Angaben first-class gemacht, keine neuen Felder ergaenzt).
        await publish_event(
            "folder.resource.moved",
            subject=folder.id,
            payload={"resource_id": folder.id, "new_parent_id": folder.parent_id},
        )
    return folder


@app.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(folder_id: str, session: AsyncSession = Depends(get_session)) -> None:
    """Sofortiger Hard-Delete - bleibt als Fallback für bereits-leere,
    nie-retention-behaftete Fälle bestehen. Der reguläre Weg ist seit
    P7-S1b `POST /folders/{folder_id}/trash`."""
    try:
        await repository.delete_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.FolderNotEmptyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    # Kein Akteur bekannt - der Endpunkt nimmt keinen deleted_by entgegen.
    await publish_event(
        "folder.resource.deleted", subject=folder_id, payload={"resource_id": folder_id}
    )


@app.post("/folders/{folder_id}/trash", response_model=TrashResult)
async def trash_folder(
    folder_id: str, payload: TrashRequest, session: AsyncSession = Depends(get_session)
) -> TrashResult:
    """Papierkorb-Weg (5.2, seit P7-S1b) - kaskadiert über den gesamten
    aktiven Teilbaum (Unterordner + enthaltene Dokumente, siehe
    repository.soft_delete_folder). Seit P7-S1c optional per generischem
    Vier-Augen-Mechanismus gegated (4.3, Aktionstyp `folder.delete`,
    unabhängig von der bereits bestehenden retentionsgetriggerten
    `folder.force_delete`) - Löschantrag-Workflow für reguläre Nutzer,
    exaktes Muster wie `document_service.main.trash_document`/
    `force_release_lock` (P6-S4). Per Default (keine Konfiguration) bleibt
    das Verhalten unverändert: sofortige Ausführung."""
    if await app.state.approval_client.requires_approval("folder.delete"):
        request = await app.state.approval_client.create_request(
            action_type="folder.delete",
            initiated_by=payload.deleted_by,
            payload={"folder_id": folder_id, "deleted_by": payload.deleted_by},
        )
        return TrashResult(status="pending_approval", approval_request_id=request["id"])

    try:
        folder = await repository.soft_delete_folder(
            session,
            folder_id,
            deleted_by=payload.deleted_by,
            document_client=app.state.document_client,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "folder.trashed",
        subject=folder_id,
        payload={"deleted_by": payload.deleted_by},
        actor=payload.deleted_by,
    )
    return TrashResult(status="trashed", folder=folder)


@app.post("/folders/{folder_id}/restore", response_model=FolderOut)
async def restore_folder(folder_id: str, session: AsyncSession = Depends(get_session)) -> FolderOut:
    """Papierkorb-Wiederherstellung (5.2, seit P7-S1b) - nur innerhalb der
    konfigurierten Frist möglich, stellt kaskadiert gelöschte Unterordner/
    Dokumente mit wieder her."""
    try:
        folder = await repository.restore_folder(
            session, folder_id, document_client=app.state.document_client
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.NotDeletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except repository.RestorePeriodExpiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    # Kein Akteur bekannt - der Endpunkt nimmt keinen restored_by entgegen.
    await publish_event("folder.restored", subject=folder_id, payload={})
    return folder


@app.put("/folders/{folder_id}/retention", response_model=FolderOut)
async def put_retention(
    folder_id: str, payload: RetentionUpdate, session: AsyncSession = Depends(get_session)
) -> FolderOut:
    """Aufbewahrung/Zwangslöschung terminieren (5.2/5.2a, seit P7-S1b) - der
    eigentliche Vollzug erfolgt asynchron über `_retention_poll_loop`."""
    try:
        folder = await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if payload.full_deletion:
        reason_required = await _resolve_deletion_reason_required(session, folder.object_type_id)
        if reason_required and not payload.reason:
            raise HTTPException(
                status_code=422,
                detail="Ein Löschgrund ist für diesen Objekttyp/diese Installation Pflicht",
            )

    updated = await repository.set_retention(
        session,
        folder_id,
        retention_until=payload.retention_until,
        full_deletion=payload.full_deletion,
        reason=payload.reason,
        notify_email=payload.notify_email,
    )
    await session.commit()
    # Kein Akteur bekannt - RetentionUpdate verfolgt bislang nicht, wer die
    # Frist geaendert hat.
    await publish_event(
        "folder.retention.updated",
        subject=folder_id,
        payload={
            "retention_until": payload.retention_until.isoformat()
            if payload.retention_until
            else None,
            "full_deletion": payload.full_deletion,
        },
    )
    return updated


@app.post("/legal-holds", response_model=LegalHoldOut, status_code=status.HTTP_201_CREATED)
async def create_legal_hold(
    payload: LegalHoldCreate, session: AsyncSession = Depends(get_session)
) -> LegalHoldOut:
    """Legal Hold setzen (5.2, seit P7-S1b) - überschreibt jede fällige
    Aktion im Poll-Loop, bis er wieder aufgehoben wird. Keine eigene
    Rollenprüfung in diesem Grundgerüst (identisches offenes Wissen wie
    document-service, P7-S1)."""
    try:
        hold = await repository.create_legal_hold(
            session, payload.folder_id, set_by=payload.set_by, reason=payload.reason
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "folder.legal_hold.set",
        subject=payload.folder_id,
        payload={"set_by": payload.set_by, "reason": payload.reason},
        actor=payload.set_by,
    )
    return hold


@app.post("/legal-holds/{hold_id}/release", response_model=LegalHoldOut)
async def release_legal_hold(
    hold_id: str, payload: LegalHoldReleaseRequest, session: AsyncSession = Depends(get_session)
) -> LegalHoldOut:
    try:
        hold = await repository.release_legal_hold(
            session, hold_id, released_by=payload.released_by
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.AlreadyReleasedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "folder.legal_hold.released",
        subject=hold.folder_id,
        payload={"released_by": payload.released_by},
        actor=payload.released_by,
    )
    return hold


@app.get("/legal-holds", response_model=list[LegalHoldOut])
async def list_legal_holds(
    folder_id: str, active_only: bool = False, session: AsyncSession = Depends(get_session)
) -> list[LegalHoldOut]:
    return await repository.list_holds(session, folder_id, active_only=active_only)


@app.get("/deletion-register", response_model=list[DeletionRegisterEntryOut])
async def get_deletion_register(
    folder_id: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[DeletionRegisterEntryOut]:
    return await repository.list_deletion_register(session, folder_id=folder_id)


@app.get("/retention-config", response_model=RetentionConfigOut)
async def get_retention_config(session: AsyncSession = Depends(get_session)) -> RetentionConfigOut:
    config = await repository.get_retention_config(session)
    await session.commit()
    return config


@app.put("/retention-config", response_model=RetentionConfigOut)
async def put_retention_config(
    body: RetentionConfigIn, session: AsyncSession = Depends(get_session)
) -> RetentionConfigOut:
    config = await repository.update_retention_config(
        session,
        deletion_reason_required=body.deletion_reason_required,
        reminder_lead_days=body.reminder_lead_days,
    )
    await session.commit()
    return config


@app.get("/trash-config", response_model=TrashConfigOut)
async def get_trash_config(session: AsyncSession = Depends(get_session)) -> TrashConfigOut:
    config = await repository.get_trash_config(session)
    await session.commit()
    return config


@app.put("/trash-config", response_model=TrashConfigOut)
async def put_trash_config(
    body: TrashConfigIn, session: AsyncSession = Depends(get_session)
) -> TrashConfigOut:
    config = await repository.update_trash_config(
        session, restore_period_days=body.restore_period_days
    )
    await session.commit()
    return config
