import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from document_service import repository, retention_actions
from document_service.approval_client import ApprovalClient
from document_service.consumer import start_consuming
from document_service.content_type_sniffer import sniff_content_type
from document_service.folder_client import FolderClient
from document_service.models import Base, Document
from document_service.object_type_client import ObjectTypeClient
from document_service.schemas import (
    CheckinResult,
    DeletionRegisterEntryOut,
    DocumentOut,
    DocumentUpdate,
    DocumentVersionOut,
    ForceReleaseResult,
    LegalHoldCreate,
    LegalHoldOut,
    LegalHoldReleaseRequest,
    LockAcquireRequest,
    LockForceReleaseRequest,
    LockOut,
    LockReleaseRequest,
    RetentionConfigIn,
    RetentionConfigOut,
    RetentionUpdate,
    TrashConfigIn,
    TrashConfigOut,
    UploadConfigIn,
    UploadConfigOut,
)
from document_service.settings import Settings
from document_service.storage_client import (
    ObjectNotFoundError,
    StorageClient,
    compute_checksum,
)
from document_service.virus_scan_client import (
    ScanRejectedError,
    ScanUnavailableError,
    VirusScanClient,
)

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


# Reservierter Attributschlüssel für den Kennzeichengenerator (2.2, P5e-S2) -
# ein vom Client mitgesendeter Wert wird bei der Anlage verworfen, die
# Vergabe erfolgt ausschließlich serverseitig über den Object-Type Service.
KENNZEICHEN_ATTRIBUTE = "Kennzeichen"


def _has_kennzeichen_admin_role(x_dms_roles: str) -> bool:
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return settings.kennzeichen_admin_role in roles


def _object_key(document_id: str, checksum_sha256: str) -> str:
    # Inhaltsadressiert statt versionsnummer-basiert (2.1a): vermeidet die
    # Henne-Ei-Reihenfolge "Upload braucht Versionsnummer, Versionsnummer
    # braucht abgeschlossenen DB-Schreibzugriff" und dedupliziert identische
    # Inhalte innerhalb desselben Dokuments automatisch.
    return f"documents/{document_id}/{checksum_sha256}"


async def _execute_or_defer_forced_deletion(session: AsyncSession, document: Document) -> None:
    """Physische Zwangslöschung (5.2a) - optional per Vier-Augen-Prinzip
    gegated (4.3, gleiches Muster wie `force_release_lock`): wird
    `document.force_delete` genehmigungspflichtig konfiguriert, legt dieser
    Tick nur EINMAL einen Freigabe-Request an (`force_delete_approval_
    requested_at` verhindert wiederholte Requests bei jedem weiteren Tick,
    solange die Genehmigung aussteht) - die eigentliche Ausführung übernimmt
    dann `consumer.py`, sobald `permission.approval.approved` eintrifft."""
    document_id = document.id
    reason = document.pending_deletion_reason
    if await app.state.approval_client.requires_approval("document.force_delete"):
        if document.force_delete_approval_requested_at is not None:
            return
        await app.state.approval_client.create_request(
            action_type="document.force_delete",
            initiated_by="system:retention-poll",
            payload={
                "document_id": document_id,
                "reason": reason,
                "triggered_by": "system:retention-poll",
            },
        )
        document.force_delete_approval_requested_at = datetime.now(UTC)
        await session.commit()
        return

    await retention_actions.execute_forced_deletion(
        session,
        app.state.storage,
        document_id,
        reason=reason,
        triggered_by="system:retention-poll",
        governance_bypass_role=settings.governance_bypass_role,
    )
    await session.commit()
    await publish_event(
        "document.force_deleted",
        document_id,
        {"reason": reason, "triggered_by": "system:retention-poll"},
    )


async def _retention_poll_loop(session_factory) -> None:
    """Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, seit P7-S1) - Poll-
    Loop statt eines echten BPMN-Prozesses (workflow-service kennt keinen
    generischen "Callback nach N Tagen"-Mechanismus außerhalb echter
    Prozessinstanzen mit Timer-/Boundary-Events, siehe ADR 0030) - gleiches
    Idiom wie workflow-service's `_sla_poll_loop` (ADR 0020, P6-S2). Ein
    Fehler in einem Tick bricht die Schleife nicht ab, damit ein einzelnes
    defektes Dokument nicht die Aufbewahrungs-Überwachung aller anderen
    stoppt. Drei unabhängige Phasen je Durchlauf: Löscherinnerung, fällige
    Aufbewahrungsfrist (Soft-Delete oder Zwangslöschung), abgelaufene
    Papierkorb-Fristen."""
    while True:
        try:
            async with session_factory() as session:
                config = await repository.get_retention_config(session)
                if config.reminder_lead_days is not None:
                    for document in await repository.list_due_for_reminder(
                        session, lead_days=config.reminder_lead_days
                    ):
                        document.deletion_reminder_sent_at = datetime.now(UTC)
                        await session.flush()
                        await session.commit()
                        await publish_event(
                            "document.deletion.reminder",
                            document.id,
                            {
                                "title": document.title,
                                "retention_until": document.retention_until.isoformat()
                                if document.retention_until
                                else None,
                                "full_deletion": document.full_deletion,
                                "notify_email": document.reminder_notify_email,
                            },
                        )

            async with session_factory() as session:
                for document in await repository.list_due_for_retention_action(session):
                    if document.full_deletion:
                        await _execute_or_defer_forced_deletion(session, document)
                        continue
                    document_id = document.id
                    await repository.delete_document(
                        session, document_id, deleted_by="system:retention-poll"
                    )
                    await session.commit()
                    await publish_event(
                        "document.deleted", document_id, {"deleted_by": "system:retention-poll"}
                    )

            async with session_factory() as session:
                trash_config = await repository.get_trash_config(session)
                for document in await repository.list_expired_trash(
                    session, restore_period_days=trash_config.restore_period_days
                ):
                    document_id = document.id
                    purged = await retention_actions.purge_expired_trash_entry(
                        session, app.state.storage, document_id
                    )
                    if purged:
                        await session.commit()
                        await publish_event(
                            "document.trash_purged", document_id, {"trigger": "trash_expiry"}
                        )
                    else:
                        await session.rollback()
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
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS document"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc-Schema-Erweiterung (kein Alembic in dieser frühen Phase, siehe
        # CONTRIBUTING.md): `create_all` legt fehlende TABELLEN an, ändert aber
        # keine bestehenden - `attributes` kam erst in P3-S3 dazu. Idempotent
        # dank IF NOT EXISTS, betrifft nur additive, defaultbehaftete Spalten.
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS attributes JSON DEFAULT '{}'::json NOT NULL"
            )
        )
        # Bearbeitungskopien (2.3, P6-S3) - additive, nullable Herkunftsfelder,
        # gleiches Ad-hoc-Migrationsmuster wie oben.
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS derived_from_document_id VARCHAR(128) "
                "REFERENCES document.document(id)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS derived_from_version_number INTEGER"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS originating_case_id VARCHAR(128)"
            )
        )
        # Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, P7-S1) - gleiches
        # Ad-hoc-Migrationsmuster wie oben. `legal_hold`/`deletion_register_entry`/
        # `retention_config`/`trash_config` sind neue Tabellen und werden
        # bereits von `create_all` angelegt.
        await conn.execute(
            text(
                "ALTER TABLE document.document ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS full_deletion BOOLEAN DEFAULT FALSE NOT NULL"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS pending_deletion_reason VARCHAR(1024)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS deletion_reminder_sent_at TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS reminder_notify_email VARCHAR(255)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS force_delete_approval_requested_at TIMESTAMPTZ"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    # Singleton-Configs einmalig vor dem ersten Request/Poll-Tick anlegen
    # (5.2/5.2a, seit P7-S1) - ohne das würden der sofort beim ersten
    # `_retention_poll_loop`-Durchlauf feuernde Zugriff und ein zeitgleicher
    # API-Aufruf (z. B. `GET /trash-config`) beide das Fehlen der Zeile sehen
    # und gleichzeitig versuchen, sie anzulegen (`get_or_create`-Race,
    # `UniqueViolationError`) - anders als bei den selten gleichzeitig
    # zugegriffenen Configs anderer Services macht der hier neu hinzukommende
    # Poll-Loop das zu einem echten Risiko.
    async with app.state.session_factory() as session:
        await repository.get_retention_config(session)
        await repository.get_trash_config(session)
        await session.commit()

    app.state.storage = StorageClient(settings.storage_service_base_url)
    app.state.folder_client = FolderClient(settings.folder_service_base_url)
    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)
    app.state.virus_scan_client = VirusScanClient(settings.virus_scan_service_base_url)
    app.state.approval_client = ApprovalClient(settings.permission_service_base_url)

    event_bus = NatsEventBusClient(settings.nats_url, stream="document")
    await event_bus.connect()
    app.state.event_bus = event_bus

    # Erster Konsument dieses Service überhaupt (P6-S4, 4.3): getrennter
    # Client (ensure_stream=False), da document-service den Stream
    # "permission" nicht selbst besitzt - gleiches Zwei-Client-Prinzip wie
    # bei notification-service/case-service.
    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    await start_consuming(
        consumer_bus,
        settings.subjects,
        app.state.session_factory,
        app.state.storage,
        settings.governance_bypass_role,
        publish_event,
    )

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    # Aufbewahrung/Legal Hold/Zwangslöschung (5.2/5.2a, seit P7-S1) - gleiches
    # Poll-Loop-Idiom wie workflow-service's SLA-Zeitüberwachung (ADR 0020).
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
    await app.state.storage.close()
    await app.state.folder_client.close()
    await app.state.object_type_client.close()
    await app.state.virus_scan_client.close()
    await app.state.approval_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(event_type: str, subject: str, payload: dict) -> None:
    event = Event(
        event_type=event_type, service_name=settings.service_name, subject=subject, payload=payload
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


async def _resolve_content_type(session: AsyncSession, data: bytes) -> str:
    """Sniffing statt ungeprüftem Client-Header (P5d-S1) + Abgleich gegen die
    admin-editierbare Format-Whitelist. Wird vor dem Virenscan aufgerufen -
    ein von vornherein abgelehntes Format muss den Scan-Dienst nicht erst
    bemühen."""
    content_type = sniff_content_type(data)
    config = await repository.get_upload_config(session)
    if config.allowed_content_types and content_type not in config.allowed_content_types:
        raise HTTPException(
            status_code=400,
            detail=f"Content-Type {content_type!r} ist nicht in der erlaubten Formatliste",
        )
    return content_type


async def _resolve_deletion_reason_required(
    session: AsyncSession, object_type_id: int | None
) -> bool:
    """Löschgrund-Pflicht (5.2a): installationsweiter Default aus
    `RetentionConfig`, je Objekttyp per `deletion_reason_required_override`
    überschreibbar (Tri-State, gleiches Muster wie
    `kennzeichen_display_override`)."""
    config = await repository.get_retention_config(session)
    if object_type_id is not None:
        object_type = await app.state.object_type_client.get(object_type_id)
        if (
            object_type is not None
            and object_type.get("deletion_reason_required_override") is not None
        ):
            return object_type["deletion_reason_required_override"]
    return config.deletion_reason_required


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/upload-config", response_model=UploadConfigOut)
async def get_upload_config(session: AsyncSession = Depends(get_session)) -> UploadConfigOut:
    config = await repository.get_upload_config(session)
    await session.commit()
    return config


@app.put("/upload-config", response_model=UploadConfigOut)
async def put_upload_config(
    body: UploadConfigIn, session: AsyncSession = Depends(get_session)
) -> UploadConfigOut:
    config = await repository.update_upload_config(
        session, allowed_content_types=body.allowed_content_types
    )
    await session.commit()
    return config


@app.post("/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_document(
    file: UploadFile = File(...),
    title: str = Form(...),
    created_by: str = Form(...),
    folder_id: str | None = Form(None),
    object_type_id: int | None = Form(None),
    attributes: str | None = Form(None),
    derived_from_document_id: str | None = Form(None),
    derived_from_version_number: int | None = Form(None),
    originating_case_id: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    try:
        parsed_attributes = json.loads(attributes) if attributes else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="attributes ist kein gültiges JSON") from exc
    parsed_attributes.pop(KENNZEICHEN_ATTRIBUTE, None)

    # Prozessspezifische Bearbeitungskopie (2.3, P6-S3, z. B. eine Schwärzung
    # für die Akteneinsicht): keine neue Version des Ursprungsdokuments,
    # sondern ein eigenständiges Dokument mit Verweis auf die konkrete
    # Ausgangsversion - durchläuft ansonsten exakt dieselbe Pipeline
    # (Virenscan, Storage, Versionierung, Audit) wie jedes andere Dokument.
    if derived_from_document_id is not None:
        if derived_from_version_number is None:
            raise HTTPException(
                status_code=400,
                detail="derived_from_version_number ist Pflicht, wenn "
                "derived_from_document_id gesetzt ist",
            )
        try:
            await repository.get_version(
                session, derived_from_document_id, derived_from_version_number
            )
        except repository.NotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    parent_folder = None
    if folder_id is not None:
        parent_folder = await app.state.folder_client.get(folder_id)
        if parent_folder is None:
            raise HTTPException(status_code=400, detail=f"folder_id {folder_id!r} unbekannt")

    retention_until: datetime | None = None
    if object_type_id is not None:
        errors = await app.state.object_type_client.validate(
            object_type_id,
            name=title,
            attributes=parsed_attributes,
            parent_object_type_id=parent_folder["object_type_id"] if parent_folder else None,
            parent_is_root=folder_id is None or folder_id == "root",
        )
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})

        kennzeichen = await app.state.object_type_client.next_kennzeichen(object_type_id)
        if kennzeichen is not None:
            parsed_attributes[KENNZEICHEN_ATTRIBUTE] = kennzeichen

        # Aufbewahrung (5.2, seit P7-S1): Typ-Default einmalig in ein
        # konkretes Datum übersetzt, keine manuelle Angabe beim Anlegen nötig
        # (kann danach jederzeit über PUT .../retention überschrieben werden).
        object_type = await app.state.object_type_client.get(object_type_id)
        if object_type and object_type.get("default_retention_days") is not None:
            retention_until = datetime.now(UTC) + timedelta(
                days=object_type["default_retention_days"]
            )

    data = await file.read()
    content_type = await _resolve_content_type(session, data)

    try:
        await app.state.virus_scan_client.scan(
            data=data,
            filename=file.filename or title,
            content_type=content_type,
            document_id=None,
            created_by=created_by,
        )
    except ScanRejectedError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "virus_detected", "threat_name": exc.threat_name}
        ) from exc
    except ScanUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Virenscan-Dienst nicht erreichbar - Upload abgelehnt"
        ) from exc

    checksum = compute_checksum(data)
    document_id = str(uuid.uuid4())
    key = _object_key(document_id, checksum)
    await app.state.storage.upload(key, data, content_type, retain_until=retention_until)

    document = await repository.create_document(
        session,
        document_id=document_id,
        title=title,
        filename=file.filename or title,
        content_type=content_type,
        size_bytes=len(data),
        checksum_sha256=checksum,
        storage_object_key=key,
        folder_id=folder_id,
        object_type_id=object_type_id,
        attributes=parsed_attributes,
        created_by=created_by,
        derived_from_document_id=derived_from_document_id,
        derived_from_version_number=derived_from_version_number,
        originating_case_id=originating_case_id,
        retention_until=retention_until,
    )
    await session.commit()
    event_payload = {"title": title, "created_by": created_by}
    if derived_from_document_id is not None:
        event_payload["derived_from_document_id"] = derived_from_document_id
    await publish_event("document.created", subject=document_id, payload=event_payload)
    return document


@app.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    folder_id: str, session: AsyncSession = Depends(get_session)
) -> list[DocumentOut]:
    return await repository.list_documents_by_folder(session, folder_id)


@app.get("/documents/deleted", response_model=list[DocumentOut])
async def list_deleted_documents(
    folder_id: str, session: AsyncSession = Depends(get_session)
) -> list[DocumentOut]:
    """Papierkorb-Inhalt eines Ordners (5.2, seit P7-S1) - Route MUSS vor
    `/documents/{document_id}` registriert sein, sonst würde FastAPI
    "deleted" fälschlich als `document_id` interpretieren."""
    return await repository.list_deleted_documents(session, folder_id)


@app.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    try:
        return await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/documents/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: str,
    payload: DocumentUpdate,
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    try:
        document = await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if payload.attributes is not None:
        old_kennzeichen = document.attributes.get(KENNZEICHEN_ATTRIBUTE)
        new_kennzeichen = payload.attributes.get(KENNZEICHEN_ATTRIBUTE)
        if new_kennzeichen != old_kennzeichen and not _has_kennzeichen_admin_role(x_dms_roles):
            raise HTTPException(
                status_code=403,
                detail=f"Nur die Rolle {settings.kennzeichen_admin_role!r} darf das "
                f"Attribut {KENNZEICHEN_ATTRIBUTE!r} ändern",
            )

    if document.object_type_id is not None:
        errors = await app.state.object_type_client.validate(
            document.object_type_id,
            name=payload.title if payload.title is not None else document.title,
            attributes=payload.attributes
            if payload.attributes is not None
            else document.attributes,
        )
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})

    updated = await repository.update_document_metadata(
        session, document_id, title=payload.title, attributes=payload.attributes
    )
    await session.commit()
    await publish_event(
        "document.metadata.updated", subject=document_id, payload={"title": updated.title}
    )
    return updated


@app.delete("/documents/{document_id}", response_model=DocumentOut)
async def delete_document(
    document_id: str, deleted_by: str, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    try:
        document = await repository.delete_document(session, document_id, deleted_by=deleted_by)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event("document.deleted", subject=document_id, payload={"deleted_by": deleted_by})
    return document


@app.post("/documents/{document_id}/restore", response_model=DocumentOut)
async def restore_document(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    """Papierkorb-Wiederherstellung (5.2, seit P7-S1) - nur innerhalb der
    konfigurierten Frist möglich (`GET/PUT /trash-config`)."""
    try:
        document = await repository.restore_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.NotDeletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except repository.RestorePeriodExpiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event("document.restored", subject=document_id, payload={})
    return document


@app.put("/documents/{document_id}/retention", response_model=DocumentOut)
async def put_retention(
    document_id: str, payload: RetentionUpdate, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    """Aufbewahrung/Zwangslöschung terminieren (5.2/5.2a, seit P7-S1) - der
    eigentliche Vollzug erfolgt asynchron über `_retention_poll_loop`, sobald
    `retention_until` erreicht ist (siehe main.py)."""
    try:
        document = await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if payload.full_deletion:
        reason_required = await _resolve_deletion_reason_required(session, document.object_type_id)
        if reason_required and not payload.reason:
            raise HTTPException(
                status_code=422,
                detail="Ein Löschgrund ist für diesen Objekttyp/diese Installation Pflicht",
            )

    updated = await repository.set_retention(
        session,
        document_id,
        retention_until=payload.retention_until,
        full_deletion=payload.full_deletion,
        reason=payload.reason,
        notify_email=payload.notify_email,
    )
    await session.commit()
    await publish_event(
        "document.retention.updated",
        subject=document_id,
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
    """Legal Hold setzen (5.2, seit P7-S1) - überschreibt jede fällige
    Aktion im Poll-Loop, bis er wieder aufgehoben wird. Keine eigene
    Rollenprüfung in diesem Grundgerüst (siehe "Offene Punkte")."""
    try:
        hold = await repository.create_legal_hold(
            session, payload.document_id, set_by=payload.set_by, reason=payload.reason
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "document.legal_hold.set",
        subject=payload.document_id,
        payload={"set_by": payload.set_by, "reason": payload.reason},
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
        "document.legal_hold.released",
        subject=hold.document_id,
        payload={"released_by": payload.released_by},
    )
    return hold


@app.get("/legal-holds", response_model=list[LegalHoldOut])
async def list_legal_holds(
    document_id: str,
    active_only: bool = False,
    session: AsyncSession = Depends(get_session),
) -> list[LegalHoldOut]:
    return await repository.list_holds(session, document_id, active_only=active_only)


@app.get("/deletion-register", response_model=list[DeletionRegisterEntryOut])
async def get_deletion_register(
    document_id: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[DeletionRegisterEntryOut]:
    """Löschregister (5.2a, seit P7-S1) - siehe docs/services/document-service.md
    zur bewusst noch fehlenden separaten Backup-Politik (Phase 11)."""
    return await repository.list_deletion_register(session, document_id=document_id)


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


@app.get("/documents/{document_id}/versions", response_model=list[DocumentVersionOut])
async def list_versions(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> list[DocumentVersionOut]:
    try:
        return await repository.list_versions(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/documents/{document_id}/versions/{version_number}", response_model=DocumentVersionOut)
async def get_version(
    document_id: str, version_number: int, session: AsyncSession = Depends(get_session)
) -> DocumentVersionOut:
    try:
        return await repository.get_version(session, document_id, version_number)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/documents/{document_id}/content")
async def download_current_content(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> Response:
    try:
        version = await repository.get_current_version(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        data = await app.state.storage.download(version.storage_object_key)
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Inhalt im Storage Service nicht (mehr) vorhanden"
        ) from exc
    return Response(content=data, media_type=version.content_type or "application/octet-stream")


@app.get("/documents/{document_id}/versions/{version_number}/content")
async def download_version_content(
    document_id: str, version_number: int, session: AsyncSession = Depends(get_session)
) -> Response:
    try:
        version = await repository.get_version(session, document_id, version_number)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        data = await app.state.storage.download(version.storage_object_key)
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Inhalt im Storage Service nicht (mehr) vorhanden"
        ) from exc
    return Response(content=data, media_type=version.content_type or "application/octet-stream")


@app.post("/documents/{document_id}/versions", response_model=CheckinResult, status_code=201)
async def checkin_version(
    document_id: str,
    file: UploadFile = File(...),
    expected_base_version_number: int = Form(...),
    created_by: str = Form(...),
    comment: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> CheckinResult:
    data = await file.read()
    content_type = await _resolve_content_type(session, data)

    try:
        await app.state.virus_scan_client.scan(
            data=data,
            filename=file.filename or f"version-{expected_base_version_number + 1}",
            content_type=content_type,
            document_id=document_id,
            created_by=created_by,
        )
    except ScanRejectedError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "virus_detected", "threat_name": exc.threat_name}
        ) from exc
    except ScanUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Virenscan-Dienst nicht erreichbar - Upload abgelehnt"
        ) from exc

    # Aufbewahrung (5.1/5.2a, seit P7-S1): trägt der Dokument bereits eine
    # Frist, bekommt auch diese neue Version dieselbe Storage-Level-Sperre
    # mit (nur beim Schreiben selbst möglich, siehe storage_client.upload
    # Docstring) - ein unbekanntes Dokument lässt `retention_until=None`.
    try:
        existing_document = await repository.get_document(session, document_id)
        retention_until = existing_document.retention_until
    except repository.NotFoundError:
        retention_until = None

    checksum = compute_checksum(data)
    key = _object_key(document_id, checksum)
    await app.state.storage.upload(key, data, content_type, retain_until=retention_until)

    try:
        version, is_conflict = await repository.checkin_version(
            session,
            document_id,
            expected_base_version_number=expected_base_version_number,
            storage_object_key=key,
            filename=file.filename or f"version-{expected_base_version_number + 1}",
            content_type=content_type,
            size_bytes=len(data),
            checksum_sha256=checksum,
            created_by=created_by,
            comment=comment,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.LockConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await session.commit()
    await publish_event(
        "document.version.created",
        subject=document_id,
        payload={
            "version_number": version.version_number,
            "is_conflict": is_conflict,
            "created_by": created_by,
        },
    )
    return CheckinResult(version=version, is_conflict=is_conflict)


@app.get("/documents/{document_id}/lock", response_model=LockOut | None)
async def get_lock(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> LockOut | None:
    return await repository.get_lock(session, document_id)


@app.post("/documents/{document_id}/lock", response_model=LockOut, status_code=201)
async def acquire_lock(
    document_id: str, payload: LockAcquireRequest, session: AsyncSession = Depends(get_session)
) -> LockOut:
    try:
        lock = await repository.acquire_lock(
            session,
            document_id,
            locked_by=payload.locked_by,
            session_id=payload.session_id,
            timeout_seconds=payload.timeout_seconds or settings.default_lock_timeout_seconds,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.LockConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return lock


@app.delete("/documents/{document_id}/lock", status_code=204)
async def release_lock(
    document_id: str, payload: LockReleaseRequest, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.release_lock(session, document_id, released_by=payload.released_by)
    except repository.LockNotHeldError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await session.commit()


@app.post("/documents/{document_id}/lock/force-release", response_model=ForceReleaseResult)
async def force_release_lock(
    document_id: str, payload: LockForceReleaseRequest, session: AsyncSession = Depends(get_session)
) -> ForceReleaseResult:
    """Administrativer Force-Unlock (4.2) - besonders sensibler Audit-Fall.
    Seit P6-S4 optional per generischem Vier-Augen-Mechanismus gegated (4.3):
    ist `document.force_unlock` in permission-service als
    genehmigungspflichtig konfiguriert, wird die Sperre NICHT sofort
    aufgehoben, sondern ein Freigabe-Request angelegt - die eigentliche
    Ausführung folgt erst über `consumer.py`, sobald das
    `permission.approval.approved`-Event eintrifft. Per Default (keine
    Konfiguration) bleibt das Verhalten unverändert: sofortige Ausführung."""
    if await app.state.approval_client.requires_approval("document.force_unlock"):
        request = await app.state.approval_client.create_request(
            action_type="document.force_unlock",
            initiated_by=payload.released_by,
            payload={
                "document_id": document_id,
                "released_by": payload.released_by,
                "reason": payload.reason,
            },
        )
        return ForceReleaseResult(status="pending_approval", approval_request_id=request["id"])

    try:
        original_lock = await repository.force_release_lock(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "document.lock.force_released",
        subject=document_id,
        payload={
            "original_locked_by": original_lock.locked_by,
            "released_by": payload.released_by,
            "reason": payload.reason,
        },
    )
    return ForceReleaseResult(status="released", lock=original_lock)
