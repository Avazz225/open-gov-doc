import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_permission_client import PermissionServiceClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
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
    FolderTemplateApplyRequest,
    FolderTemplateApplyResult,
    FolderTemplateCreate,
    FolderTemplateDetailOut,
    FolderTemplateOut,
    FolderUpdate,
    LegalHoldCreate,
    LegalHoldOut,
    LegalHoldReleaseRequest,
    ReconcileRestoreDeletionRequest,
    RetentionConfigIn,
    RetentionConfigOut,
    RetentionUpdate,
    TrashConfigIn,
    TrashConfigOut,
    TrashRequest,
    TrashResult,
)
from folder_service.settings import PROTECTED_FOLDER_IDS, ROOT_FOLDER_ID, Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def _resolve_deletion_reason_required(
    session: AsyncSession, object_type_id: int | None
) -> bool:
    """Deletion reason requirement (5.2a) - installation-wide default from
    `RetentionConfig`, overridable per object type via
    `deletion_reason_required_override`. Identical pattern to
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
    """Physical forced folder deletion (5.2a, since P7-S1b) - unlike
    documents (P7-S1), this additionally checks whether the subtree still
    contains active subfolders/documents: if so, the forced deletion is
    skipped for this tick (no automatic cascading forced deletion of
    contained objects, see docs/services/folder-service.md "Open Points").
    Otherwise identical four-eyes pattern to
    `document_service.main._execute_or_defer_forced_deletion`."""
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
    """Retention/legal hold/forced deletion for folders (5.2/5.2a, since
    P7-S1b) - identical poll-loop idiom to `document_service.main.
    _retention_poll_loop` (P7-S1, itself following ADR 0020)."""
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
        # Ad-hoc schema extension (no Alembic in this early phase, see
        # CONTRIBUTING.md) - retention/legal hold/forced deletion (5.2/5.2a,
        # since P7-S1b), same pattern as document-service (P7-S1).
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
        # Personal trash (2.5, P15-S1) - same ad-hoc migration pattern.
        await conn.execute(
            text("ALTER TABLE folder.folder ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(128)")
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    async with app.state.session_factory() as session:
        await repository.ensure_root_folder(session)
        # Inbox/Outbox (2.5/3.3, P15-S3).
        await repository.ensure_special_folders(session)
        # Create singleton configs once before the first request/poll tick
        # (race avoidance, see document-service P7-S1).
        await repository.get_retention_config(session)
        await repository.get_trash_config(session)
        await session.commit()

    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)
    app.state.document_client = DocumentClient(settings.document_service_base_url)
    app.state.approval_client = ApprovalClient(settings.permission_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)

    # Sensor concept (10.1, full rollout): a fresh `SensorConfigClient` per
    # startup, bound into the module-level `sensor_config_proxy` (its
    # httpx client can't outlive the event loop it was first used on, see
    # `SensorConfigProxy`'s docstring) - not a module-level client itself.
    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    event_bus = NatsEventBusClient(settings.nats_url, stream="folder")
    await event_bus.connect()
    app.state.event_bus = event_bus

    # First consumer of this service at all (5.2a, since P7-S1b) - separate
    # client (ensure_stream=False), since folder-service does not own the
    # "permission" stream itself (same two-client principle as document-service).
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
        sensors=http_sensor_declarations(),
    )

    retention_poll_task = asyncio.create_task(_retention_poll_loop(app.state.session_factory))

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    retention_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await retention_poll_task
    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    await consumer_bus.close()
    await event_bus.close()
    await app.state.object_type_client.close()
    await app.state.document_client.close()
    await app.state.approval_client.close()
    await app.state.permission_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

# Sensor concept (10.1, full rollout): must run at module level, right
# after `app` is constructed - see bootstrap_http_sensors's docstring
# for why this can't move into `lifespan` (FastAPI forbids adding
# middleware once the app has started).
sensor_config_proxy, sensor_registry, _http_requests_sensor, _http_duration_sensor = (
    bootstrap_http_sensors(app, settings.service_name)
)


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
    parent_object_type_id: int | None = None,
    parent_is_root: bool = False,
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


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


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

    # Retention (5.2, since P7-S1): type default translated once into a
    # concrete date, identical pattern to document-service. Has already
    # applied since P7-S1 across object types for `applies_to="folder"`.
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
    parent_id: str | None = None,
    scope: str | None = None,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[FolderOut]:
    """Trash contents of a folder (5.2, since P7-S1b) - route MUST be
    registered before `/folders/{folder_id}`, otherwise FastAPI would
    incorrectly interpret "deleted" as `folder_id` (same trap as in
    document-service, see main.py there). Without `scope`, unchanged
    behavior (no auth check, `parent_id` required) - all existing
    callers/tests remain unaffected. Since P15-S1 (2.5), two additional
    installation-wide views explicitly requested via `scope`: `personal`
    (only one's own deletion markers) and `admin` (full trash, deletion
    administration) - no `admin_classified` variant as in document-service,
    concept 2.5 marks only documents as classified documents, not folders."""
    if scope is None:
        if parent_id is None:
            raise HTTPException(
                status_code=422, detail="parent_id ist ohne scope-Parameter erforderlich"
            )
        return await repository.list_deleted_folders(session, parent_id=parent_id)

    if scope == "personal":
        if not x_dms_principal:
            raise HTTPException(status_code=401, detail="X-DMS-Principal fehlt")
        return await repository.list_deleted_folders(
            session, parent_id=parent_id, deleted_by=x_dms_principal
        )

    if scope == "admin":
        roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
        if settings.trash_hard_delete_admin_role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Nur die Rolle {settings.trash_hard_delete_admin_role!r} darf den "
                "vollständigen Papierkorb einsehen",
            )
        return await repository.list_deleted_folders(session, parent_id=parent_id)

    raise HTTPException(status_code=422, detail=f"Unbekannter scope {scope!r}")


@app.post("/folders/{folder_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
async def purge_folder(
    folder_id: str,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Manual, immediate permanent deletion from trash (2.5, P15-S1) -
    requires deletion administration, independent of the automatic
    `_retention_poll_loop` (which, after `TrashConfig.
    restore_period_days` expires, calls the same
    `retention_actions.purge_expired_trash_entry` with
    `trigger="trash_expiry"` - here `trigger="manual_purge"` with the real
    principal as `triggered_by`). Same safety check as forced deletion
    (`_execute_or_defer_forced_deletion`): deletion only actually happens
    once the subtree no longer contains any active subfolders/documents."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="X-DMS-Principal fehlt")
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    if settings.trash_hard_delete_admin_role not in roles:
        raise HTTPException(
            status_code=403,
            detail=f"Nur die Rolle {settings.trash_hard_delete_admin_role!r} darf Ordner "
            "endgültig aus dem Papierkorb löschen",
        )
    try:
        folder = await repository.get_folder_any_state(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if folder.deleted_at is None:
        raise HTTPException(status_code=409, detail="Ordner befindet sich nicht im Papierkorb")

    subtree_ids = await repository.list_active_subtree_ids(session, folder_id)
    if (
        await repository.has_any_child_folder_row(session, folder_id)
        or await app.state.document_client.count_active(subtree_ids) > 0
    ):
        raise HTTPException(
            status_code=409,
            detail="Teilbaum enthält noch aktive Unterordner/Dokumente - erst leeren",
        )

    await retention_actions.purge_expired_trash_entry(
        session, folder_id, trigger="manual_purge", triggered_by=x_dms_principal
    )
    await session.commit()
    await publish_event(
        "folder.trash_purged",
        folder_id,
        {"trigger": "manual_purge", "triggered_by": x_dms_principal},
        actor=x_dms_principal,
    )


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

    # Inbox/Outbox (2.5, P15-S3): must not be renamed or moved - "a special
    # area exists exactly once per installation", a rename/move would break
    # the UI-side lookup via the fixed ID (see user-ui PoststellePane).
    if folder_id in PROTECTED_FOLDER_IDS and (
        payload.name is not None or payload.parent_id is not None
    ):
        raise HTTPException(
            status_code=409,
            detail=f"Sonderordner {folder_id!r} kann nicht umbenannt/verschoben werden",
        )

    # Bugfix (P14-S12, found while researching bulk metadata editing, 8):
    # constraint checking (4.5) previously applied ONLY on an actual move
    # (`is_move`) - a pure attribute/name change without a move bypassed
    # object-type validation entirely, unlike document-service's
    # `update_document` (there, `object_type_client.validate(...)` runs on
    # EVERY PATCH request as soon as an object type is set - placement
    # parameters are only populated on a move, otherwise empty). Now
    # symmetric with documents: validates on every change, not just on move.
    is_move = payload.parent_id is not None and payload.parent_id != current.parent_id
    if current.object_type_id is not None:
        placement_kwargs: dict = {}
        if is_move:
            try:
                new_parent = await repository.get_folder(session, payload.parent_id)
            except repository.NotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            placement_kwargs = {
                "parent_object_type_id": new_parent.object_type_id,
                "parent_is_root": payload.parent_id == ROOT_FOLDER_ID,
            }
        await _validate_against_object_type(
            current.object_type_id,
            name=payload.name if payload.name is not None else current.name,
            attributes=payload.attributes if payload.attributes is not None else current.attributes,
            **placement_kwargs,
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
        # No actor known - FolderUpdate does not yet track who triggered the
        # move/rename (P7-S2: only made already-existing data first-class,
        # no new fields added).
        await publish_event(
            "folder.resource.moved",
            subject=folder.id,
            payload={"resource_id": folder.id, "new_parent_id": folder.parent_id},
        )
    return folder


@app.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(folder_id: str, session: AsyncSession = Depends(get_session)) -> None:
    """Immediate hard delete - remains as a fallback for already-empty
    cases that never had retention applied. The regular path since P7-S1b
    is `POST /folders/{folder_id}/trash`."""
    if folder_id in PROTECTED_FOLDER_IDS:
        raise HTTPException(
            status_code=409, detail=f"Sonderordner {folder_id!r} kann nicht gelöscht werden"
        )
    try:
        await repository.delete_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.FolderNotEmptyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    # No actor known - the endpoint does not accept a deleted_by.
    await publish_event(
        "folder.resource.deleted", subject=folder_id, payload={"resource_id": folder_id}
    )


@app.post("/folders/{folder_id}/trash", response_model=TrashResult)
async def trash_folder(
    folder_id: str, payload: TrashRequest, session: AsyncSession = Depends(get_session)
) -> TrashResult:
    """Trash path (5.2, since P7-S1b) - cascades over the entire active
    subtree (subfolders + contained documents, see
    repository.soft_delete_folder). Since P7-S1c, optionally gated via the
    generic four-eyes mechanism (4.3, action type `folder.delete`,
    independent of the already-existing retention-triggered
    `folder.force_delete`) - deletion request workflow for regular users,
    exact pattern as `document_service.main.trash_document`/
    `force_release_lock` (P6-S4). By default (no configuration), behavior
    remains unchanged: immediate execution."""
    if folder_id in PROTECTED_FOLDER_IDS:
        raise HTTPException(
            status_code=409,
            detail=f"Sonderordner {folder_id!r} kann nicht in den Papierkorb verschoben werden",
        )
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
    """Trash restore (5.2, since P7-S1b) - only possible within the
    configured retention period, also restores subfolders/documents that
    were deleted via cascade."""
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
    # No actor known - the endpoint does not accept a restored_by.
    await publish_event("folder.restored", subject=folder_id, payload={})
    return folder


@app.put("/folders/{folder_id}/retention", response_model=FolderOut)
async def put_retention(
    folder_id: str, payload: RetentionUpdate, session: AsyncSession = Depends(get_session)
) -> FolderOut:
    """Schedule retention/forced deletion (5.2/5.2a, since P7-S1b) - actual
    enforcement happens asynchronously via `_retention_poll_loop`."""
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
    # No actor known - RetentionUpdate does not yet track who changed the
    # retention period.
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


async def _require_legal_hold_permission(x_dms_principal: str) -> None:
    """RBAC (Post-Roadmap Phase 19 Session 10, ADR 0075) - identical pattern
    to `document-service`'s helper of the same name: checks the new
    domain-admin capability `admin.legal_hold`, deliberately NOT in the
    "everyone" group. `GET /legal-holds` remains ungated."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    if not await app.state.permission_client.has_permission(x_dms_principal, "admin.legal_hold"):
        raise HTTPException(
            status_code=403, detail="Fehlende Domain-Admin-Rolle 'Legal-Hold-Verwaltung'"
        )


@app.post("/legal-holds", response_model=LegalHoldOut, status_code=status.HTTP_201_CREATED)
async def create_legal_hold(
    payload: LegalHoldCreate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> LegalHoldOut:
    """Set a legal hold (5.2, since P7-S1b) - overrides any due action in
    the poll loop until it is released again. Since P19-S10 gated by
    `admin.legal_hold`, see `_require_legal_hold_permission`."""
    await _require_legal_hold_permission(x_dms_principal)
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
    hold_id: str,
    payload: LegalHoldReleaseRequest,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> LegalHoldOut:
    await _require_legal_hold_permission(x_dms_principal)
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


def _has_admin_role(x_dms_roles: str) -> bool:
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return settings.admin_role in roles


@app.post(
    "/folders/{folder_id}/reconcile-restore-deletion",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reconcile_restore_deletion(
    folder_id: str,
    payload: ReconcileRestoreDeletionRequest,
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Deletion reconciliation after restore (10.4, P11-S4) -
    structurally identical to
    `document_service.main.reconcile_restore_deletion`: "the same mechanism
    as for the original forced deletion" (10.4 verbatim)."""
    if not _has_admin_role(x_dms_roles):
        raise HTTPException(
            status_code=403,
            detail=f"Nur die Rolle {settings.admin_role!r} darf einen Löschabgleich "
            "nach Restore auslösen",
        )
    try:
        # Check existence first (see document-service counterpart) -
        # otherwise execute_forced_deletion creates an orphaned register
        # entry before the 404 kicks in.
        await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await retention_actions.execute_forced_deletion(
        session,
        folder_id,
        reason=payload.reason,
        triggered_by="system:restore-reconciliation",
    )
    await session.commit()
    await publish_event(
        "folder.force_deleted",
        folder_id,
        {
            "reason": payload.reason,
            "triggered_by": "system:restore-reconciliation",
            "reconciliation_of_entry_id": payload.original_entry_id,
        },
        actor="system:restore-reconciliation",
    )


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


# --- Structure templates (2.5/7.3, since P15-S6) ---


@app.post(
    "/folder-templates", response_model=FolderTemplateOut, status_code=status.HTTP_201_CREATED
)
async def create_folder_template(
    payload: FolderTemplateCreate, session: AsyncSession = Depends(get_session)
) -> FolderTemplateOut:
    """Captures the active subtree starting at `payload.source_folder_id`
    as a named, reusable structure template (2.5/7.3, e.g. a file plan
    skeleton) - see `repository.build_template_structure`/ADR 0056."""
    try:
        structure = await repository.build_template_structure(session, payload.source_folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    template = await repository.create_template(
        session,
        name=payload.name,
        description=payload.description,
        structure=structure,
        created_by=payload.created_by,
    )
    await session.commit()
    return template


@app.get("/folder-templates", response_model=list[FolderTemplateOut])
async def list_folder_templates(
    session: AsyncSession = Depends(get_session),
) -> list[FolderTemplateOut]:
    return await repository.list_templates(session)


@app.get("/folder-templates/{template_id}", response_model=FolderTemplateDetailOut)
async def get_folder_template(
    template_id: str, session: AsyncSession = Depends(get_session)
) -> FolderTemplateDetailOut:
    try:
        return await repository.get_template(session, template_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/folder-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder_template(
    template_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.delete_template(session, template_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


@app.post("/folder-templates/{template_id}/apply", response_model=FolderTemplateApplyResult)
async def apply_folder_template(
    template_id: str,
    payload: FolderTemplateApplyRequest,
    session: AsyncSession = Depends(get_session),
) -> FolderTemplateApplyResult:
    """Applies a structure template below `payload.target_parent_id` -
    creates real folders (see `repository.apply_template`) and publishes a
    regular `folder.resource.created` event for each of them, so that
    `permission-service`'s `ResourceNode` tree stays in sync (identical
    event to a single `POST /folders`)."""
    try:
        template = await repository.get_template(session, template_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        created = await repository.apply_template(
            session,
            template,
            target_parent_id=payload.target_parent_id,
            created_by=payload.created_by,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    for folder in created:
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
    return FolderTemplateApplyResult(root_folder=created[0], created_count=len(created))
