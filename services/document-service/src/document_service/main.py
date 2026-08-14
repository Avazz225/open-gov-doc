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
from dms_metrics_client import SensorConfigClient, metrics_payload, run_gauge_sampler_loop
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

from document_service import metrics, repository, retention_actions
from document_service.approval_client import ApprovalClient
from document_service.consumer import start_consuming
from document_service.content_type_sniffer import sniff_content_type
from document_service.folder_client import FolderClient
from document_service.html_preview_guard import rewrite_external_references
from document_service.license_client import LicenseLimitClient
from document_service.models import Base, Document
from document_service.object_type_client import MissingKennzeichenAttributeError, ObjectTypeClient
from document_service.permission_client import PermissionServiceClient
from document_service.schemas import (
    ArchiveStatusOut,
    AuditTraceConfigIn,
    AuditTraceConfigOut,
    AuditTraceRoleOverrideIn,
    AuditTraceRoleOverrideOut,
    CascadeRestoreRequest,
    CascadeResult,
    CascadeTrashRequest,
    CheckinResult,
    CountActiveRequest,
    CountActiveResult,
    DeletionRegisterEntryOut,
    DocumentOut,
    DocumentUpdate,
    DocumentVersionOut,
    ForceReleaseResult,
    HasActiveHoldOut,
    LegalHoldCreate,
    LegalHoldOut,
    LegalHoldReleaseRequest,
    LockAcquireRequest,
    LockForceReleaseRequest,
    LockOut,
    LockReleaseRequest,
    MarkArchivedRequest,
    PublicShareLinkOut,
    ReconcileRestoreDeletionRequest,
    RetentionConfigIn,
    RetentionConfigOut,
    RetentionUpdate,
    ShareLinkConfigIn,
    ShareLinkConfigOut,
    ShareLinkCreate,
    ShareLinkOut,
    TrashConfigIn,
    TrashConfigOut,
    TrashRequest,
    TrashResult,
    UploadConfigIn,
    UploadConfigOut,
    WebdavEditTokenOut,
    WebdavEditTokenResolveOut,
    WebdavEditTokenSummary,
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


# Reserved attribute key for the reference number generator (2.2, P5e-S2) -
# a value sent by the client is discarded on creation; assignment happens
# exclusively server-side via the Object-Type Service.
KENNZEICHEN_ATTRIBUTE = "Kennzeichen"


def _has_kennzeichen_admin_role(x_dms_roles: str) -> bool:
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return settings.kennzeichen_admin_role in roles


def _has_quarantine_release_role(x_dms_roles: str) -> bool:
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return settings.quarantine_release_admin_role in roles


def _is_html_content_type(content_type: str | None) -> bool:
    """Post-roadmap Phase 21 Session 3 (ADR 0086) - decides whether
    `rewrite_external_references` must apply before delivery.
    Parameter suffixes (``; charset=...``) are stripped; `python-magic`
    usually doesn't include them anyway, but this is done as a safety
    measure regardless."""
    if not content_type:
        return False
    return content_type.split(";", 1)[0].strip().lower() == "text/html"


async def _should_log_document_access(
    session: AsyncSession, category: str, x_dms_roles: str
) -> bool:
    """Forensic trace (5.4b, since P7-S2c): decides whether a `document.
    viewed`/`document.downloaded` action should be logged for the current
    caller - base configuration + role overrides from the gateway-injected
    `X-DMS-Roles` header, see repository.resolve_should_log."""
    config = await repository.get_audit_trace_config(session)
    overrides = await repository.list_role_overrides(session)
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return repository.resolve_should_log(category, roles, config, overrides)


def _object_key(document_id: str, checksum_sha256: str) -> str:
    # Content-addressed instead of version-number-based (2.1a): avoids the
    # chicken-and-egg ordering "upload needs a version number, version number
    # needs a completed DB write" and automatically deduplicates identical
    # content within the same document.
    return f"documents/{document_id}/{checksum_sha256}"


async def _execute_or_defer_forced_deletion(session: AsyncSession, document: Document) -> None:
    """Physical forced deletion (5.2a) - optionally gated by the four-eyes
    principle (4.3, same pattern as `force_release_lock`): if
    `document.force_delete` is configured to require approval, this tick
    creates an approval request only ONCE (`force_delete_approval_
    requested_at` prevents repeated requests on every subsequent tick while
    approval is pending) - the actual execution is then handled by
    `consumer.py` once `permission.approval.approved` arrives."""
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
        actor="system:retention-poll",
    )


async def _retention_poll_loop(session_factory) -> None:
    """Retention/legal hold/forced deletion (5.2/5.2a, since P7-S1) - poll
    loop instead of a real BPMN process (workflow-service has no generic
    "callback after N days" mechanism outside of actual process instances
    with timer/boundary events, see ADR 0030) - same idiom as
    workflow-service's `_sla_poll_loop` (ADR 0020, P6-S2). An error in one
    tick doesn't abort the loop, so a single broken document doesn't stop
    retention monitoring for all others. Three independent phases per pass:
    deletion reminder, due retention period (soft delete or forced
    deletion), expired trash deadlines."""
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
                            actor="system:retention-poll",
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
                        "document.deleted",
                        document_id,
                        {"deleted_by": "system:retention-poll"},
                        actor="system:retention-poll",
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
                            "document.trash_purged",
                            document_id,
                            {"trigger": "trash_expiry"},
                            actor="system:retention-poll",
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
        # Ad-hoc schema extension (no Alembic in this early phase, see
        # CONTRIBUTING.md): `create_all` creates missing TABLES but doesn't
        # alter existing ones - `attributes` was only added in P3-S3. Idempotent
        # thanks to IF NOT EXISTS, only affects additive columns with defaults.
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS attributes JSON DEFAULT '{}'::json NOT NULL"
            )
        )
        # Working copies (2.3, P6-S3) - additive, nullable provenance fields,
        # same ad-hoc migration pattern as above.
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
        # Retention/legal hold/forced deletion (5.2/5.2a, P7-S1) - same
        # ad-hoc migration pattern as above. `legal_hold`/`deletion_register_entry`/
        # `retention_config`/`trash_config` are new tables and are already
        # created by `create_all`.
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
        # Cascade provenance for folder trash (5.2, since P7-S1b).
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS deleted_via_folder_id VARCHAR(128)"
            )
        )
        # Records disposal (5.6, since P7-S3) - same ad-hoc migration pattern.
        await conn.execute(
            text("ALTER TABLE document.document ADD COLUMN IF NOT EXISTS archive_after TIMESTAMPTZ")
        )
        await conn.execute(
            text("ALTER TABLE document.document ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ")
        )
        await conn.execute(
            text(
                "ALTER TABLE document.document ADD COLUMN IF NOT EXISTS archive_format VARCHAR(16)"
            )
        )
        await conn.execute(
            text("ALTER TABLE document.document ADD COLUMN IF NOT EXISTS dehydrated_at TIMESTAMPTZ")
        )
        # Personal trash (2.5, P15-S1) - same ad-hoc migration pattern.
        await conn.execute(
            text("ALTER TABLE document.document ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(128)")
        )
        # Schema drift correction (P15-S1, found live): in long-running
        # installations (including this dev environment), `object_type_id`
        # from a very early project phase still carries the VARCHAR type from
        # back then, even though the model has long since declared `Integer`
        # - `create_all` never changes the type of already-existing columns.
        # Unnoticeable with simple equality comparisons (implicit cast by
        # asyncpg), but `object_type_id NOT IN (...)` (new for the classified
        # documents trash filter) then fails with "operator does not exist:
        # character varying <> integer". Only executed when the column type
        # actually still differs (no lock/rewrite on every startup on
        # installations that are already corrected).
        drift = await conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = 'document' AND table_name = 'document' "
                "AND column_name = 'object_type_id'"
            )
        )
        if drift.scalar() == "character varying":
            await conn.execute(
                text(
                    "ALTER TABLE document.document ALTER COLUMN object_type_id "
                    "TYPE INTEGER USING object_type_id::integer"
                )
            )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    # Create singleton configs once, before the first request/poll tick
    # (5.2/5.2a, since P7-S1) - without this, the access firing immediately on
    # the first `_retention_poll_loop` pass and a concurrent API call (e.g.
    # `GET /trash-config`) could both see the row missing and try to create it
    # at the same time (`get_or_create` race, `UniqueViolationError`) - unlike
    # the rarely concurrently accessed configs of other services, the poll
    # loop newly added here makes this a real risk.
    async with app.state.session_factory() as session:
        await repository.get_retention_config(session)
        await repository.get_trash_config(session)
        await session.commit()

    app.state.storage = StorageClient(settings.storage_service_base_url)
    app.state.folder_client = FolderClient(settings.folder_service_base_url)
    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)
    app.state.virus_scan_client = VirusScanClient(settings.virus_scan_service_base_url)
    app.state.approval_client = ApprovalClient(settings.permission_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.license_limit_client = LicenseLimitClient(
        settings.license_service_base_url, settings.license_limit_cache_ttl_seconds
    )

    # Sensor concept (10.1, P11-S1): document-service is one of the two
    # pilots (see P11-S0 finding). Activation status comes from
    # `monitoring-service`, TTL poll instead of NATS invalidation (deliberate
    # scope decision, same pattern as `registry-service`).
    app.state.sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await app.state.sensor_config_client.start()
    sensor_registry, upload_duration_sensor, active_documents_gauge = metrics.build_sensor_registry(
        app.state.sensor_config_client
    )
    app.state.sensor_registry = sensor_registry
    app.state.upload_duration_sensor = upload_duration_sensor
    samplers = metrics.build_samplers(active_documents_gauge, app.state.session_factory)
    sensor_sampler_task = asyncio.create_task(
        run_gauge_sampler_loop(samplers, interval_seconds=settings.sensor_sample_interval_seconds)
    )

    event_bus = NatsEventBusClient(settings.nats_url, stream="document")
    await event_bus.connect()
    app.state.event_bus = event_bus

    # First consumer of this service at all (P6-S4, 4.3): separate client
    # (ensure_stream=False), since document-service doesn't own the
    # "permission" stream itself - same two-client principle as
    # notification-service/case-service.
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
        sensors=metrics.sensor_declarations(),
    )

    # Retention/legal hold/forced deletion (5.2/5.2a, since P7-S1) - same
    # poll loop idiom as workflow-service's SLA time monitoring (ADR 0020).
    retention_poll_task = asyncio.create_task(_retention_poll_loop(app.state.session_factory))

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    retention_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await retention_poll_task
    sensor_sampler_task.cancel()
    with suppress(asyncio.CancelledError):
        await sensor_sampler_task
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    await consumer_bus.close()
    await event_bus.close()
    await app.state.storage.close()
    await app.state.folder_client.close()
    await app.state.object_type_client.close()
    await app.state.virus_scan_client.close()
    await app.state.approval_client.close()
    await app.state.permission_client.close()
    await app.state.license_limit_client.close()
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


async def _resolve_content_type(session: AsyncSession, data: bytes) -> str:
    """Sniffing instead of an unchecked client header (P5d-S1) + comparison
    against the admin-editable format whitelist. Called before the virus
    scan - a format rejected outright doesn't need to bother the scan
    service first."""
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
    """Deletion reason requirement (5.2a): installation-wide default from
    `RetentionConfig`, overridable per object type via
    `deletion_reason_required_override` (tri-state, same pattern as
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


@app.get("/metrics")
def get_metrics() -> Response:
    """Prometheus exposition of the two in-house sensors (10.1, P11-S1) -
    scraped by `monitoring-service`, not directly by Prometheus."""
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


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


@app.get("/audit-trace-config", response_model=AuditTraceConfigOut)
async def get_audit_trace_config(
    session: AsyncSession = Depends(get_session),
) -> AuditTraceConfigOut:
    config = await repository.get_audit_trace_config(session)
    await session.commit()
    return config


@app.put("/audit-trace-config", response_model=AuditTraceConfigOut)
async def put_audit_trace_config(
    body: AuditTraceConfigIn, session: AsyncSession = Depends(get_session)
) -> AuditTraceConfigOut:
    config = await repository.update_audit_trace_config(
        session, log_viewed=body.log_viewed, log_downloaded=body.log_downloaded
    )
    await session.commit()
    return config


@app.get("/audit-trace-role-overrides", response_model=list[AuditTraceRoleOverrideOut])
async def list_audit_trace_role_overrides(
    session: AsyncSession = Depends(get_session),
) -> list[AuditTraceRoleOverrideOut]:
    overrides = await repository.list_role_overrides(session)
    await session.commit()
    return overrides


@app.put("/audit-trace-role-overrides/{role}", response_model=AuditTraceRoleOverrideOut)
async def put_audit_trace_role_override(
    role: str, body: AuditTraceRoleOverrideIn, session: AsyncSession = Depends(get_session)
) -> AuditTraceRoleOverrideOut:
    override = await repository.upsert_role_override(
        session, role, log_viewed=body.log_viewed, log_downloaded=body.log_downloaded
    )
    await session.commit()
    return override


@app.delete("/audit-trace-role-overrides/{role}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_audit_trace_role_override(
    role: str, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.delete_role_override(session, role)
        await session.commit()
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/documents/due-for-archival", response_model=list[DocumentOut])
async def list_documents_due_for_archival(
    session: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    """Internal call from `archival-service` (5.6, since P7-S3) - candidates
    for the next poll tick."""
    return await repository.list_due_for_archival(session)


@app.post("/documents/{document_id}/archive-request", response_model=DocumentOut)
async def request_document_archive(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    """Manual records disposal trigger (5.6) - sets `archive_after` to now,
    if not already due."""
    try:
        document = await repository.request_archive(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return document


@app.get("/documents/{document_id}/archive-status", response_model=ArchiveStatusOut)
async def get_document_archive_status(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> ArchiveStatusOut:
    try:
        document = await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return ArchiveStatusOut(
        document_id=document.id,
        archive_after=document.archive_after,
        archived_at=document.archived_at,
        archive_format=document.archive_format,
        dehydrated_at=document.dehydrated_at,
    )


@app.get("/documents/{document_id}/has-active-hold", response_model=HasActiveHoldOut)
async def get_document_has_active_hold(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> HasActiveHoldOut:
    """Internal call from `archival-service` (5.6) - an active legal hold
    (5.2) blocks the dehydration step, not the creation of the archive copy
    itself."""
    return HasActiveHoldOut(has_active_hold=await repository.has_active_hold(session, document_id))


@app.put("/documents/{document_id}/archived", response_model=DocumentOut)
async def mark_document_archived(
    document_id: str, payload: MarkArchivedRequest, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    """Callback from `archival-service` once the archive copy has been
    verified (5.6)."""
    try:
        document = await repository.mark_archived(
            session, document_id, archive_format=payload.archive_format
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "document.archived",
        document_id,
        {"archive_format": payload.archive_format},
        actor="system:archival-service",
    )
    return document


@app.put("/documents/{document_id}/dehydrated", response_model=DocumentOut)
async def mark_document_dehydrated(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    """Callback from `archival-service` after the live storage copy was
    removed once the transition period expired (5.6)."""
    try:
        document = await repository.mark_dehydrated(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event("document.dehydrated", document_id, {}, actor="system:archival-service")
    return document


@app.put("/documents/{document_id}/rehydrated", response_model=DocumentOut)
async def mark_document_rehydrated(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    """Callback from `archival-service` after successful retrieval (5.6)."""
    try:
        document = await repository.mark_rehydrated(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event("document.rehydrated", document_id, {}, actor="system:archival-service")
    return document


async def _prepare_document_fields(
    session: AsyncSession,
    *,
    title: str,
    folder_id: str | None,
    object_type_id: int | None,
    attributes: str | None,
    derived_from_document_id: str | None,
    derived_from_version_number: int | None,
) -> tuple[dict, datetime | None, datetime | None]:
    """Shared validation/derivation for every document creation path -
    identical for `POST /documents` and `POST /documents/
    from-quarantine-release` (2.5, P15-S2), which differ only in the scan
    step."""
    try:
        parsed_attributes = json.loads(attributes) if attributes else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="attributes ist kein gültiges JSON") from exc
    parsed_attributes.pop(KENNZEICHEN_ATTRIBUTE, None)

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
    archive_after: datetime | None = None
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

        try:
            kennzeichen = await app.state.object_type_client.next_kennzeichen(
                object_type_id, parsed_attributes
            )
        except MissingKennzeichenAttributeError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if kennzeichen is not None:
            parsed_attributes[KENNZEICHEN_ATTRIBUTE] = kennzeichen

        # Retention (5.2, since P7-S1): type default translated once into a
        # concrete date, no manual entry needed at creation time (can be
        # overridden at any time afterwards via PUT .../retention).
        object_type = await app.state.object_type_client.get(object_type_id)
        if object_type and object_type.get("default_retention_days") is not None:
            retention_until = datetime.now(UTC) + timedelta(
                days=object_type["default_retention_days"]
            )
        # Records disposal (5.6, since P7-S3): independent of retention_until,
        # complementary to the regular retention period (see docstring above).
        if object_type and object_type.get("default_archive_after_days") is not None:
            archive_after = datetime.now(UTC) + timedelta(
                days=object_type["default_archive_after_days"]
            )

    return parsed_attributes, retention_until, archive_after


async def _persist_new_document(
    session: AsyncSession,
    *,
    data: bytes,
    filename: str,
    content_type: str | None,
    title: str,
    created_by: str,
    folder_id: str | None,
    object_type_id: int | None,
    attributes: dict,
    derived_from_document_id: str | None,
    derived_from_version_number: int | None,
    originating_case_id: str | None,
    retention_until: datetime | None,
    archive_after: datetime | None,
    event_type: str = "document.created",
    extra_event_payload: dict | None = None,
) -> Document:
    checksum = compute_checksum(data)
    document_id = str(uuid.uuid4())
    key = _object_key(document_id, checksum)
    await app.state.storage.upload(key, data, content_type, retain_until=retention_until)

    document = await repository.create_document(
        session,
        document_id=document_id,
        title=title,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        checksum_sha256=checksum,
        storage_object_key=key,
        folder_id=folder_id,
        object_type_id=object_type_id,
        attributes=attributes,
        created_by=created_by,
        derived_from_document_id=derived_from_document_id,
        derived_from_version_number=derived_from_version_number,
        originating_case_id=originating_case_id,
        retention_until=retention_until,
        archive_after=archive_after,
    )
    await session.commit()
    event_payload = {"title": title, "created_by": created_by, "folder_id": folder_id}
    if derived_from_document_id is not None:
        event_payload["derived_from_document_id"] = derived_from_document_id
    if extra_event_payload:
        event_payload.update(extra_event_payload)
    await publish_event(event_type, subject=document_id, payload=event_payload, actor=created_by)
    return document


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
    # Sensor "document.upload.duration" (10.1, P11-S1): start time is only
    # captured if the sensor is currently active - when disabled, even this
    # minimal overhead is avoided entirely.
    upload_sensor = app.state.upload_duration_sensor
    upload_started_at = time.monotonic() if upload_sensor.is_active() else None

    # License limit block (concept 9.3, P9-S2): only genuine new creations,
    # not versioning/restoration of existing documents - "doesn't
    # retroactively block existing data, but prevents new creations".
    if await app.state.license_limit_client.is_exceeded("documents"):
        raise HTTPException(status_code=403, detail="Dokumentenlimit der Lizenz überschritten")

    parsed_attributes, retention_until, archive_after = await _prepare_document_fields(
        session,
        title=title,
        folder_id=folder_id,
        object_type_id=object_type_id,
        attributes=attributes,
        derived_from_document_id=derived_from_document_id,
        derived_from_version_number=derived_from_version_number,
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

    document = await _persist_new_document(
        session,
        data=data,
        filename=file.filename or title,
        content_type=content_type,
        title=title,
        created_by=created_by,
        folder_id=folder_id,
        object_type_id=object_type_id,
        attributes=parsed_attributes,
        derived_from_document_id=derived_from_document_id,
        derived_from_version_number=derived_from_version_number,
        originating_case_id=originating_case_id,
        retention_until=retention_until,
        archive_after=archive_after,
    )
    if upload_started_at is not None:
        upload_sensor.observe(time.monotonic() - upload_started_at)
    return document


@app.post(
    "/documents/from-quarantine-release",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_document_from_quarantine_release(
    file: UploadFile = File(...),
    title: str = Form(...),
    created_by: str = Form(...),
    folder_id: str | None = Form(None),
    object_type_id: int | None = Form(None),
    attributes: str | None = Form(None),
    source_scan_id: str = Form(...),
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    """Internal creation path exclusively for virus-scan-service when
    releasing a quarantine case that has been cleared as a false positive
    (2.5/10.3, P15-S2). Deliberately does NOT trigger another scan via
    `virus_scan_client` (unlike `POST /documents`): the file has already
    been scanned, and precisely this scan (now classified as a false
    positive) is the reason for the release - a repeat scan would reproduce
    the same finding and make the release structurally impossible (see ADR
    0052). Has its own role gate (`quarantine_release_admin_role`) as a
    second instance alongside the role check in virus-scan-service itself,
    which forwards the roles of the original caller - no endpoint that
    bypasses the mandatory virus scan should depend on a single check
    alone."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="X-DMS-Principal fehlt")
    if not _has_quarantine_release_role(x_dms_roles):
        raise HTTPException(
            status_code=403,
            detail=f"Nur die Rolle {settings.quarantine_release_admin_role!r} darf einen "
            "Quarantäne-Fall freigeben",
        )
    if await app.state.license_limit_client.is_exceeded("documents"):
        raise HTTPException(status_code=403, detail="Dokumentenlimit der Lizenz überschritten")

    parsed_attributes, retention_until, archive_after = await _prepare_document_fields(
        session,
        title=title,
        folder_id=folder_id,
        object_type_id=object_type_id,
        attributes=attributes,
        derived_from_document_id=None,
        derived_from_version_number=None,
    )

    data = await file.read()
    content_type = await _resolve_content_type(session, data)

    return await _persist_new_document(
        session,
        data=data,
        filename=file.filename or title,
        content_type=content_type,
        title=title,
        created_by=created_by,
        folder_id=folder_id,
        object_type_id=object_type_id,
        attributes=parsed_attributes,
        derived_from_document_id=None,
        derived_from_version_number=None,
        originating_case_id=None,
        retention_until=retention_until,
        archive_after=archive_after,
        event_type="document.created_from_quarantine_release",
        extra_event_payload={"source_scan_id": source_scan_id},
    )


@app.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    folder_id: str, session: AsyncSession = Depends(get_session)
) -> list[DocumentOut]:
    return await repository.list_documents_by_folder(session, folder_id)


@app.get("/documents/by-kennzeichen", response_model=list[DocumentOut])
async def list_documents_by_kennzeichen(
    value: str, session: AsyncSession = Depends(get_session)
) -> list[DocumentOut]:
    """Cross-object-type reference number search (2.5/3.3, P15-S3) - route
    MUST be registered before `/documents/{document_id}`, otherwise FastAPI
    would incorrectly interpret `"by-kennzeichen"` as `document_id` (same
    pattern as `/documents/deleted`). Returns a list instead of a single
    match - `Kennzeichen` is not globally unique (see
    `repository.list_documents_by_kennzeichen`), the caller (e.g.
    `mail-connector`) must check for 0/1/N matches itself."""
    return await repository.list_documents_by_kennzeichen(session, value)


@app.get("/documents/deleted", response_model=list[DocumentOut])
async def list_deleted_documents(
    folder_id: str | None = None,
    scope: str | None = None,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentOut]:
    """Trash content of a folder (5.2, since P7-S1) - route MUST be
    registered before `/documents/{document_id}`, otherwise FastAPI would
    incorrectly interpret "deleted" as `document_id`. Without `scope`,
    behavior is unchanged (no auth check, `folder_id` required) - all
    existing callers/tests remain unaffected. Since P15-S1 (2.5), three
    additional installation-wide views explicitly requested via `scope`:
    `personal` (only your own deletion markers, personal trash), `admin`
    (complete but non-classified trash, regular deletion administration),
    and `admin_classified` (structurally separate classified documents
    trash) - `folder_id` remains additionally usable as an optional filter
    in all three."""
    if scope is None:
        if folder_id is None:
            raise HTTPException(
                status_code=422, detail="folder_id ist ohne scope-Parameter erforderlich"
            )
        return await repository.list_deleted_documents(session, folder_id=folder_id)

    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}

    if scope == "personal":
        if not x_dms_principal:
            raise HTTPException(status_code=401, detail="X-DMS-Principal fehlt")
        return await repository.list_deleted_documents(
            session, folder_id=folder_id, deleted_by=x_dms_principal
        )

    if scope == "admin":
        if settings.trash_hard_delete_admin_role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Nur die Rolle {settings.trash_hard_delete_admin_role!r} darf den "
                "vollständigen Papierkorb einsehen",
            )
        classified_ids = await app.state.object_type_client.list_classified_document_type_ids()
        return await repository.list_deleted_documents(
            session, folder_id=folder_id, exclude_object_type_ids=classified_ids
        )

    if scope == "admin_classified":
        if settings.classified_trash_hard_delete_admin_role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Nur die Rolle {settings.classified_trash_hard_delete_admin_role!r} darf "
                "den Verschlusssachen-Papierkorb einsehen",
            )
        classified_ids = await app.state.object_type_client.list_classified_document_type_ids()
        return await repository.list_deleted_documents(
            session, folder_id=folder_id, include_object_type_ids=classified_ids
        )

    raise HTTPException(status_code=422, detail=f"Unbekannter scope {scope!r}")


@app.post("/documents/{document_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
async def purge_document(
    document_id: str,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Manual, immediate permanent deletion from trash (2.5, P15-S1) -
    requires deletion administration (regular or classified documents,
    depending on `ObjectType.classification_level`), independent of the
    automatic `_retention_poll_loop` (which calls the same
    `retention_actions.purge_expired_trash_entry` with
    `trigger="trash_expiry"` once `TrashConfig.restore_period_days` has
    elapsed - here `trigger="manual_purge"` with the real principal as
    `triggered_by`)."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="X-DMS-Principal fehlt")
    try:
        document = await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if document.deleted_at is None:
        raise HTTPException(status_code=409, detail="Dokument befindet sich nicht im Papierkorb")

    is_classified = False
    if document.object_type_id is not None:
        object_type = await app.state.object_type_client.get(document.object_type_id)
        # Any level being set (regardless of which one) triggers the same
        # gate as the purely binary `is_classified` used up to P17-S1
        # (P17-S2, 14.2).
        is_classified = bool(object_type and object_type.get("classification_level"))
    required_role = (
        settings.classified_trash_hard_delete_admin_role
        if is_classified
        else settings.trash_hard_delete_admin_role
    )
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    if required_role not in roles:
        raise HTTPException(
            status_code=403,
            detail=f"Nur die Rolle {required_role!r} darf Dokumente endgültig aus dem "
            "Papierkorb löschen",
        )

    purged = await retention_actions.purge_expired_trash_entry(
        session,
        app.state.storage,
        document_id,
        trigger="manual_purge",
        triggered_by=x_dms_principal,
    )
    if not purged:
        raise HTTPException(
            status_code=409,
            detail="Löschung durch eine aktive Governance-Mode-Sperre blockiert",
        )
    await session.commit()
    await publish_event(
        "document.trash_purged",
        document_id,
        {"trigger": "manual_purge", "triggered_by": x_dms_principal},
        actor=x_dms_principal,
    )


@app.post("/documents/cascade-trash", response_model=CascadeResult)
async def cascade_trash(
    payload: CascadeTrashRequest, session: AsyncSession = Depends(get_session)
) -> CascadeResult:
    """Internal service-to-service call from `folder-service` (5.2, since
    P7-S1b): soft-deletes all active documents in the given folders when
    their folder (including subtree) is moved to trash - synchronous
    instead of event-based, so an immediate `GET /documents/deleted`
    afterwards is already consistent. No own role check, same trust
    relationship as the existing `FolderClient` call in the reverse
    direction."""
    document_ids = await repository.cascade_trash_by_folder_ids(
        session,
        payload.folder_ids,
        via_folder_id=payload.via_folder_id,
        deleted_by=payload.deleted_by,
    )
    await session.commit()
    for document_id in document_ids:
        await publish_event(
            "document.deleted",
            subject=document_id,
            payload={"deleted_by": payload.deleted_by},
            actor=payload.deleted_by,
        )
    return CascadeResult(document_ids=document_ids)


@app.post("/documents/cascade-restore", response_model=CascadeResult)
async def cascade_restore(
    payload: CascadeRestoreRequest, session: AsyncSession = Depends(get_session)
) -> CascadeResult:
    """Counterpart to `cascade_trash` - when restoring a folder, restores
    only the documents that were cascade-deleted as a result."""
    document_ids = await repository.cascade_restore_by_via_folder_id(session, payload.via_folder_id)
    await session.commit()
    # No actor known - CascadeRestoreRequest doesn't yet track who triggered
    # the underlying folder restoration (P7-S2: only made already-existing
    # data first-class, no new fields added).
    for document_id in document_ids:
        await publish_event("document.restored", subject=document_id, payload={})
    return CascadeResult(document_ids=document_ids)


@app.post("/documents/count-active", response_model=CountActiveResult)
async def count_active(
    payload: CountActiveRequest, session: AsyncSession = Depends(get_session)
) -> CountActiveResult:
    """Non-empty check before forced folder deletion (5.2a, since P7-S1b) -
    `folder-service` checks before physically removing a folder whether its
    subtree still contains active documents."""
    count = await repository.count_active_by_folder_ids(session, payload.folder_ids)
    return CountActiveResult(count=count)


@app.get("/documents/count-active-total", response_model=CountActiveResult)
async def count_active_total(session: AsyncSession = Depends(get_session)) -> CountActiveResult:
    """Installation-wide document count (9.1, since P9-S1) - ungated for
    `license-service`'s internal usage check, no folder filter."""
    count = await repository.count_active_total(session)
    return CountActiveResult(count=count)


@app.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: str,
    x_dms_roles: str = Header(default=""),
    x_dms_username: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    try:
        document = await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if await _should_log_document_access(session, "viewed", x_dms_roles):
        await publish_event("document.viewed", document_id, {}, actor=x_dms_username or None)
    await session.commit()
    return document


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

    # Move (P12-S1, WebDAV connector user request): only if the folder
    # actually changes - existence/placement constraint check analogous to
    # the existing creation path (`app.state.folder_client.get(...)`, see
    # `create_document` above) instead of a new, second implementation.
    is_move = payload.folder_id is not None and payload.folder_id != document.folder_id
    target_parent_folder = None
    if is_move:
        target_parent_folder = await app.state.folder_client.get(payload.folder_id)
        if target_parent_folder is None:
            raise HTTPException(
                status_code=400, detail=f"folder_id {payload.folder_id!r} unbekannt"
            )

    if document.object_type_id is not None:
        placement_kwargs = (
            {
                "parent_object_type_id": target_parent_folder["object_type_id"],
                "parent_is_root": payload.folder_id == "root",
            }
            if is_move
            else {}
        )
        errors = await app.state.object_type_client.validate(
            document.object_type_id,
            name=payload.title if payload.title is not None else document.title,
            attributes=payload.attributes
            if payload.attributes is not None
            else document.attributes,
            **placement_kwargs,
        )
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})

    updated = await repository.update_document_metadata(
        session,
        document_id,
        title=payload.title,
        attributes=payload.attributes,
        folder_id=payload.folder_id if is_move else None,
    )
    await session.commit()
    # No actor known - DocumentUpdate doesn't yet track who changed the
    # metadata (see cascade_restore comment above).
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
    await publish_event(
        "document.deleted",
        subject=document_id,
        payload={"deleted_by": deleted_by},
        actor=deleted_by,
    )
    return document


@app.post("/documents/{document_id}/trash", response_model=TrashResult)
async def trash_document(
    document_id: str, payload: TrashRequest, session: AsyncSession = Depends(get_session)
) -> TrashResult:
    """Deletion request workflow for regular users (5.2, since P7-S1c) -
    optionally gated by the generic four-eyes mechanism (4.3, action type
    `document.delete`, independent of the already existing
    retention-triggered `document.force_delete`): if `document.delete` is
    configured to require approval, it is NOT deleted immediately, but an
    approval request is created instead - the actual execution then follows
    via `consumer.py` once `permission.approval.approved` arrives. By
    default (no configuration), behavior is unchanged: immediate execution,
    the exact same pattern as `force_release_lock` (4.2, P6-S4). Alongside
    the existing `DELETE /documents/{id}` - this endpoint remains unchanged
    as an ungated path, since no frontend calls it at all so far."""
    if await app.state.approval_client.requires_approval("document.delete"):
        request = await app.state.approval_client.create_request(
            action_type="document.delete",
            initiated_by=payload.deleted_by,
            payload={"document_id": document_id, "deleted_by": payload.deleted_by},
        )
        return TrashResult(status="pending_approval", approval_request_id=request["id"])

    try:
        document = await repository.delete_document(
            session, document_id, deleted_by=payload.deleted_by
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "document.deleted",
        subject=document_id,
        payload={"deleted_by": payload.deleted_by},
        actor=payload.deleted_by,
    )
    return TrashResult(status="trashed", document=document)


@app.post("/documents/{document_id}/restore", response_model=DocumentOut)
async def restore_document(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    """Trash restoration (5.2, since P7-S1) - only possible within the
    configured deadline (`GET/PUT /trash-config`)."""
    try:
        document = await repository.restore_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.NotDeletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except repository.RestorePeriodExpiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    # No actor known - the endpoint doesn't yet accept a restored_by
    # parameter (see cascade_restore comment above).
    await publish_event("document.restored", subject=document_id, payload={})
    return document


@app.put("/documents/{document_id}/retention", response_model=DocumentOut)
async def put_retention(
    document_id: str, payload: RetentionUpdate, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    """Schedule retention/forced deletion (5.2/5.2a, since P7-S1) - the
    actual enforcement happens asynchronously via `_retention_poll_loop`,
    once `retention_until` is reached (see main.py)."""
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
    # No actor known - RetentionUpdate doesn't yet track who changed the
    # deadline (see cascade_restore comment above).
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


async def _require_legal_hold_permission(x_dms_principal: str) -> None:
    """RBAC (post-roadmap Phase 19 Session 10, ADR 0075) - setting/releasing
    a legal hold previously had NO permission check at all. Checks the new
    domain admin capability `admin.legal_hold` (role "domain-admin-legal-
    hold") - deliberately NOT in the "everyone" group, a legal hold is a
    legally significant, administrative action (5.2), not regular business
    use. `GET /legal-holds` remains deliberately ungated - any user viewing
    a document must be able to see whether/why it is held."""
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
    """Set a legal hold (5.2, since P7-S1) - overrides any due action in
    the poll loop until it is released again. Gated by `admin.legal_hold`
    since P19-S10, see `_require_legal_hold_permission`."""
    await _require_legal_hold_permission(x_dms_principal)
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
        "document.legal_hold.released",
        subject=hold.document_id,
        payload={"released_by": payload.released_by},
        actor=payload.released_by,
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
    """Deletion register (5.2a, since P7-S1) - see docs/services/document-service.md
    regarding the deliberately still-missing separate backup policy (Phase 11)."""
    return await repository.list_deletion_register(session, document_id=document_id)


@app.post(
    "/documents/{document_id}/reconcile-restore-deletion",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def reconcile_restore_deletion(
    document_id: str,
    payload: ReconcileRestoreDeletionRequest,
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Deletion reconciliation after restore (10.4, P11-S4): a restore to a
    point in time BEFORE this already-executed forced deletion would
    unintentionally bring the document back to life - this endpoint
    executes "the same mechanism as for the original forced deletion" (10.4
    verbatim) again, instead of maintaining a second, potentially divergent
    implementation. Gated the same way as the reference number field
    (P5e-S2) - no new PermissionServiceClient for such a rare, purely
    operational endpoint."""
    if not _has_kennzeichen_admin_role(x_dms_roles):
        raise HTTPException(
            status_code=403,
            detail=f"Nur die Rolle {settings.kennzeichen_admin_role!r} darf einen "
            "Löschabgleich nach Restore auslösen",
        )
    try:
        # Check existence first: execute_forced_deletion creates the
        # DeletionRegisterEntry BEFORE it removes the document row -
        # without this upfront check, an unknown document_id would leave
        # behind an orphaned register entry before the 404 kicks in.
        await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    await retention_actions.execute_forced_deletion(
        session,
        app.state.storage,
        document_id,
        reason=payload.reason,
        triggered_by="system:restore-reconciliation",
        governance_bypass_role=settings.governance_bypass_role,
    )
    await session.commit()
    await publish_event(
        "document.force_deleted",
        document_id,
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


def _has_share_link_admin_role(x_dms_roles: str) -> bool:
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return settings.share_link_revoke_admin_role in roles


@app.get("/share-link-config", response_model=ShareLinkConfigOut)
async def get_share_link_config(session: AsyncSession = Depends(get_session)) -> ShareLinkConfigOut:
    config = await repository.get_share_link_config(session)
    await session.commit()
    return config


@app.put("/share-link-config", response_model=ShareLinkConfigOut)
async def put_share_link_config(
    body: ShareLinkConfigIn, session: AsyncSession = Depends(get_session)
) -> ShareLinkConfigOut:
    config = await repository.update_share_link_config(
        session, enabled=body.enabled, max_validity_days=body.max_validity_days
    )
    await session.commit()
    return config


@app.post("/documents/{document_id}/share-links", response_model=ShareLinkOut, status_code=201)
async def create_share_link(
    document_id: str,
    body: ShareLinkCreate,
    x_dms_principal: str = Header(default=""),
    x_dms_username: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> ShareLinkOut:
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")

    config = await repository.get_share_link_config(session)
    if not config.enabled:
        # Concept 4.2a: when disabled, "no API route reachable" - implemented
        # here as `404` instead of `403` (a FastAPI route cannot be
        # unregistered at runtime without a restart), see ADR 0047.
        raise HTTPException(status_code=404, detail="Not Found")

    try:
        document = await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    now = datetime.now(UTC)
    if body.expires_at <= now:
        raise HTTPException(status_code=400, detail="expires_at muss in der Zukunft liegen")
    if body.expires_at > now + timedelta(days=config.max_validity_days):
        raise HTTPException(
            status_code=400,
            detail=(
                f"expires_at darf höchstens {config.max_validity_days} Tage in der Zukunft liegen"
            ),
        )

    allowed = await app.state.permission_client.check_read(
        principal_id=x_dms_principal, resource_id=document.folder_id or "root"
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Kein Leserecht auf dieses Dokument")

    link = await repository.create_share_link(
        session, document_id=document_id, created_by=x_dms_principal, expires_at=body.expires_at
    )
    await session.commit()
    await publish_event(
        "document.share_link.created",
        document_id,
        {"token": link.token, "expires_at": link.expires_at.isoformat()},
        actor=x_dms_username or x_dms_principal,
    )
    return link


@app.get("/documents/{document_id}/share-links", response_model=list[ShareLinkOut])
async def list_share_links(
    document_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[ShareLinkOut]:
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")

    config = await repository.get_share_link_config(session)
    if not config.enabled:
        raise HTTPException(status_code=404, detail="Not Found")

    try:
        document = await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    allowed = await app.state.permission_client.check_read(
        principal_id=x_dms_principal, resource_id=document.folder_id or "root"
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Kein Leserecht auf dieses Dokument")

    links = await repository.list_share_links_for_document(session, document_id)
    await session.commit()
    return links


@app.delete("/share-links/{token}", status_code=204)
async def revoke_share_link_endpoint(
    token: str,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")

    link = await repository.get_share_link(session, token)
    if link is None:
        raise HTTPException(status_code=404, detail=f"Freigabelink {token!r} unbekannt")
    if link.created_by != x_dms_principal and not _has_share_link_admin_role(x_dms_roles):
        raise HTTPException(
            status_code=403,
            detail="Nur die erzeugende Person oder eine Admin-Rolle darf diesen Link widerrufen",
        )

    await repository.revoke_share_link(session, token, revoked_by=x_dms_principal)
    await session.commit()
    await publish_event(
        "document.share_link.revoked", link.document_id, {"token": token}, actor=x_dms_principal
    )


# --- Office direct editing (post-roadmap feature, WebDAV edit token) -------
# `ms-word:ofe|u|<url>`/`ms-excel:ofe|u|<url>`/`ms-powerpoint:ofe|u|<url>`
# against `webdav-connector` - structurally modeled on the share link block
# above, but NOT purely read-only (see `check_write` instead of
# `check_read`) and with a significantly more generous validity duration
# (see settings.py).


@app.post(
    "/documents/{document_id}/webdav-edit-tokens",
    response_model=WebdavEditTokenOut,
    status_code=201,
)
async def create_webdav_edit_token(
    document_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> WebdavEditTokenOut:
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")

    try:
        document = await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Write permission, not just read permission - this token grants actual
    # editing capability (check-in via WebDAV PUT), unlike a share link.
    allowed = await app.state.permission_client.check_write(
        principal_id=x_dms_principal, resource_id=document.folder_id or "root"
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Kein Schreibrecht auf dieses Dokument")

    expires_at = datetime.now(UTC) + timedelta(hours=settings.webdav_edit_token_ttl_hours)
    edit_token = await repository.create_webdav_edit_token(
        session, document_id=document_id, principal_id=x_dms_principal, expires_at=expires_at
    )
    await session.commit()
    await publish_event(
        "document.webdav_edit_token.created",
        document_id,
        {"token": edit_token.token, "expires_at": edit_token.expires_at.isoformat()},
        actor=x_dms_principal,
    )
    return edit_token


@app.get("/documents/{document_id}/webdav-edit-tokens", response_model=list[WebdavEditTokenSummary])
async def list_webdav_edit_tokens(
    document_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[WebdavEditTokenSummary]:
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")

    try:
        document = await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    allowed = await app.state.permission_client.check_write(
        principal_id=x_dms_principal, resource_id=document.folder_id or "root"
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Kein Schreibrecht auf dieses Dokument")

    tokens = await repository.list_webdav_edit_tokens_for_document(session, document_id)
    await session.commit()
    return tokens


@app.delete("/webdav-edit-tokens/{token}", status_code=204)
async def revoke_webdav_edit_token_endpoint(
    token: str,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")

    edit_token = await repository.get_webdav_edit_token(session, token)
    if edit_token is None:
        raise HTTPException(status_code=404, detail=f"WebDAV-Edit-Token {token!r} unbekannt")
    if edit_token.principal_id != x_dms_principal and not _has_share_link_admin_role(x_dms_roles):
        raise HTTPException(
            status_code=403,
            detail="Nur die anfordernde Person oder eine Admin-Rolle darf dieses Token widerrufen",
        )

    await repository.revoke_webdav_edit_token(session, token, revoked_by=x_dms_principal)
    await session.commit()
    await publish_event(
        "document.webdav_edit_token.revoked",
        edit_token.document_id,
        {"token": token},
        actor=x_dms_principal,
    )


@app.get(
    "/internal/webdav-edit-tokens/{token}",
    response_model=WebdavEditTokenResolveOut,
)
async def resolve_webdav_edit_token(
    token: str, session: AsyncSession = Depends(get_session)
) -> WebdavEditTokenResolveOut:
    """Purely east-west: called directly by `webdav-connector`'s
    `DmsAuthDomainController` against `document_service_base_url` (no
    gateway, no `X-DMS-Principal` - the token itself IS the authentication
    here). A repeat permission check is deliberately not needed (only
    checked at issuance, see ADR) - only expiry/revocation and the
    document's continued existence are checked here."""
    edit_token = await repository.get_webdav_edit_token(session, token)
    if edit_token is None:
        raise HTTPException(status_code=404, detail="WebDAV-Edit-Token unbekannt")
    if not repository.is_webdav_edit_token_active(edit_token, datetime.now(UTC)):
        detail = (
            "WebDAV-Edit-Token widerrufen"
            if edit_token.revoked_at
            else "WebDAV-Edit-Token abgelaufen"
        )
        raise HTTPException(status_code=410, detail=detail)
    try:
        await repository.get_document(session, edit_token.document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Dokument nicht mehr vorhanden") from exc
    return WebdavEditTokenResolveOut(
        document_id=edit_token.document_id, principal_id=edit_token.principal_id
    )


# --- Public, unauthenticated endpoints (4.2a) --------------------------------
# Reachable via the gateway's `public_routes` exception list (no
# X-DMS-Principal, since anonymous) - see docs/services/gateway-service.md.


async def _resolve_active_share_link(session: AsyncSession, token: str):
    config = await repository.get_share_link_config(session)
    if not config.enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    link = await repository.get_share_link(session, token)
    if link is None:
        raise HTTPException(status_code=404, detail="Freigabelink unbekannt")
    if not repository.is_share_link_active(link, datetime.now(UTC)):
        detail = "Freigabelink widerrufen" if link.revoked_at else "Freigabelink abgelaufen"
        raise HTTPException(status_code=410, detail=detail)
    try:
        document = await repository.get_document(session, link.document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Dokument nicht mehr vorhanden") from exc
    return link, document


@app.get("/public/share-links", response_model=PublicShareLinkOut)
async def get_public_share_link(
    token: str, session: AsyncSession = Depends(get_session)
) -> PublicShareLinkOut:
    link, document = await _resolve_active_share_link(session, token)
    version = await repository.get_current_version(session, document.id)
    await publish_event("document.share_link.viewed", document.id, {"token": token})
    await session.commit()
    return PublicShareLinkOut(
        title=document.title,
        content_type=version.content_type,
        size_bytes=version.size_bytes,
        expires_at=link.expires_at,
    )


@app.get("/public/share-links/content")
async def get_public_share_link_content(
    token: str, session: AsyncSession = Depends(get_session)
) -> Response:
    link, document = await _resolve_active_share_link(session, token)
    version = await repository.get_current_version(session, document.id)
    try:
        data = await app.state.storage.download(version.storage_object_key)
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Inhalt im Storage Service nicht (mehr) vorhanden"
        ) from exc
    await publish_event("document.share_link.accessed", document.id, {"token": token})
    await session.commit()
    return Response(content=data, media_type=version.content_type or "application/octet-stream")


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
    document_id: str,
    x_dms_roles: str = Header(default=""),
    x_dms_username: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        document = await repository.get_document(session, document_id)
        version = await repository.get_current_version(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if document.dehydrated_at is not None:
        raise HTTPException(
            status_code=409,
            detail="Dokumentinhalt wurde ausgesondert und muss erst zurückgeholt werden",
        )
    try:
        data = await app.state.storage.download(version.storage_object_key)
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Inhalt im Storage Service nicht (mehr) vorhanden"
        ) from exc
    if _is_html_content_type(version.content_type):
        data = rewrite_external_references(data)
    if await _should_log_document_access(session, "downloaded", x_dms_roles):
        await publish_event(
            "document.downloaded",
            document_id,
            {"version_number": version.version_number},
            actor=x_dms_username or None,
        )
    await session.commit()
    return Response(content=data, media_type=version.content_type or "application/octet-stream")


@app.get("/documents/{document_id}/versions/{version_number}/content")
async def download_version_content(
    document_id: str,
    version_number: int,
    x_dms_roles: str = Header(default=""),
    x_dms_username: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    try:
        document = await repository.get_document(session, document_id)
        version = await repository.get_version(session, document_id, version_number)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if document.dehydrated_at is not None:
        raise HTTPException(
            status_code=409,
            detail="Dokumentinhalt wurde ausgesondert und muss erst zurückgeholt werden",
        )
    try:
        data = await app.state.storage.download(version.storage_object_key)
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Inhalt im Storage Service nicht (mehr) vorhanden"
        ) from exc
    if _is_html_content_type(version.content_type):
        data = rewrite_external_references(data)
    if await _should_log_document_access(session, "downloaded", x_dms_roles):
        await publish_event(
            "document.downloaded",
            document_id,
            {"version_number": version_number},
            actor=x_dms_username or None,
        )
    await session.commit()
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

    # Retention (5.1/5.2a, since P7-S1): if the document already carries a
    # deadline, this new version also gets the same storage-level lock
    # (only possible at write time, see storage_client.upload docstring) -
    # an unknown document leaves `retention_until=None`.
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
        actor=created_by,
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
    """Administrative force unlock (4.2) - a particularly sensitive audit
    case. Since P6-S4 optionally gated by the generic four-eyes mechanism
    (4.3): if `document.force_unlock` is configured in permission-service to
    require approval, the lock is NOT released immediately, but an approval
    request is created instead - the actual execution then follows via
    `consumer.py` once the `permission.approval.approved` event arrives. By
    default (no configuration), behavior is unchanged: immediate
    execution."""
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
        actor=payload.released_by,
    )
    return ForceReleaseResult(status="released", lock=original_lock)
