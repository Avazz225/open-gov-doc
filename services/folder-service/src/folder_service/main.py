import asyncio
import base64
import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta

from dms_common import (
    SERVICE_PRINCIPALS,
    ActingIdentity,
    configure_logging,
    require_acting_identity,
)
from dms_common.otel import setup_tracing
from dms_constraint_engine import ROOT_PARENT_TYPE, validate_parent
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    SensorSpec,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_permission_client import PermissionServiceClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from folder_service import crypto, repository, retention_actions, structure_report
from folder_service.approval_client import ApprovalClient
from folder_service.consumer import start_consuming
from folder_service.document_client import DocumentClient
from folder_service.models import Base, Folder
from folder_service.object_type_client import ObjectTypeClient
from folder_service.schemas import (
    AuditTraceConfigIn,
    AuditTraceConfigOut,
    DeletionRegisterEntryOut,
    FolderCreate,
    FolderDocumentReferenceAdd,
    FolderDocumentReferenceOut,
    FolderDocumentReferenceRemove,
    FolderOut,
    FolderRetypeRequest,
    FolderTemplateApplyRequest,
    FolderTemplateApplyResult,
    FolderTemplateCreate,
    FolderTemplateDetailOut,
    FolderTemplateOut,
    FolderTemplateUpdate,
    FolderTypesRequest,
    FolderUpdate,
    LegalHoldCreate,
    LegalHoldOut,
    LegalHoldReleaseRequest,
    PseudonymizeAttributeRequest,
    PseudonymizedAttributeOut,
    ReconcileRestoreDeletionRequest,
    RetentionConfigIn,
    RetentionConfigOut,
    RetentionUpdate,
    RetypeFailure,
    RetypeResult,
    RevealAttributeRequest,
    RevealedAttributeOut,
    TrashConfigIn,
    TrashConfigOut,
    TrashRequest,
    TrashResult,
)
from folder_service.settings import (
    INBOX_FOLDER_ID,
    OUTBOX_FOLDER_ID,
    PROTECTED_FOLDER_IDS,
    ROOT_FOLDER_ID,
    Settings,
)

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
    # Phase 44 Session 2/ADR 0163: distinct from the business event above -
    # this is the structure-tree lifecycle event `structure_consumer.py`
    # (permission-service) listens for. Only `DELETE /folders/{id}` (the
    # one call site written when this event type was introduced) ever
    # published it; this forced-deletion path never did, leaving an
    # orphaned `ResourceNode` behind on every forced deletion (ADR 0154's
    # own documented, then-untracked finding).
    await publish_event(
        "folder.resource.deleted",
        subject=folder_id,
        payload={"resource_id": folder_id},
        actor="system:retention-poll",
    )


async def _retention_poll_loop(session_factory) -> None:
    """Retention/legal hold/forced deletion for folders (5.2/5.2a, since
    P7-S1b) - identical poll-loop idiom to `document_service.main.
    _retention_poll_loop` (P7-S1, itself following ADR 0020). Since
    Post-Roadmap Phase 44 Session 3 (4.8, ADR 0164): skips the whole tick
    while maintenance mode is active - forced deletion is exactly the
    kind of write an emergency lockdown exists to stop, and this loop has
    zero gateway involvement (ADR 0152's "Category B"). The check itself
    lives INSIDE the existing `try` (not before it, same precedent
    `workflow-service`'s own `_sla_poll_loop` already established) -
    unwrapped, a transient `permission-service` error would kill this
    whole tick's exception isolation instead of just skipping gracefully."""
    while True:
        try:
            if await app.state.permission_client.is_maintenance_active():
                await asyncio.sleep(settings.retention_poll_interval_seconds)
                continue
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
                    if folder.retention_pseudonymize:
                        pseudonymized_names = await _pseudonymize_eligible_attributes(
                            session,
                            folder,
                            app.state.object_type_client,
                            triggered_by="system:retention-poll",
                            reason="Automatische Pseudonymisierung bei Fristablauf (5.2)",
                        )
                        await repository.mark_retention_pseudonymized(session, folder.id)
                        await session.commit()
                        if pseudonymized_names:
                            await publish_event(
                                "folder.retention.pseudonymized",
                                folder.id,
                                {"attribute_names": pseudonymized_names},
                                actor="system:retention-poll",
                            )
                        continue
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
                    # Phase 44 Session 2/ADR 0163: see the forced-deletion
                    # path above - same missing structure-tree event, same
                    # fix.
                    await publish_event(
                        "folder.resource.deleted",
                        subject=folder_id,
                        payload={"resource_id": folder_id},
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
        # Automatic retention-expiry pseudonymization (5.2, Phase 58 Session 1).
        await conn.execute(
            text(
                "ALTER TABLE folder.folder "
                "ADD COLUMN IF NOT EXISTS retention_pseudonymize BOOLEAN DEFAULT FALSE NOT NULL"
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
        # Filing structure (P91-S2b, ADR 0255): area roots outside it.
        await conn.execute(
            text(
                "ALTER TABLE folder.folder "
                "ADD COLUMN IF NOT EXISTS structure_exempt BOOLEAN NOT NULL DEFAULT false"
            )
        )
        # Personal trash (2.5, P15-S1) - same ad-hoc migration pattern.
        await conn.execute(
            text("ALTER TABLE folder.folder ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(128)")
        )
        # Deletion-reason catalog (post-roadmap phase 31 session 1, ADR
        # 0112) - same ad-hoc migration pattern.
        await conn.execute(
            text(
                "ALTER TABLE folder.retention_config "
                "ADD COLUMN IF NOT EXISTS deletion_reason_catalog JSON DEFAULT '[]'::json NOT NULL"
            )
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

    # Post-Roadmap Phase 38 Session 4 (ADR 0149): inbox/outbox (unlike every
    # other folder) are bootstrapped directly via `ensure_special_folders`
    # above, not through `create_folder` - so they never went through the
    # `"folder.resource.created"` event that registers a `ResourceNode` in
    # permission-service. Before this session that silently didn't matter
    # (folder-service ran no permission check at all); now the resource-
    # tree ancestor walk breaks immediately for an unregistered
    # `resource_id` (denies outright rather than falling back to root), so
    # this idempotent, synchronous registration (same `POST /resources`
    # primitive as `case-service`'s/`teamspace-service`'s own resource
    # bootstrap) is needed on every startup.
    for folder_id in (INBOX_FOLDER_ID, OUTBOX_FOLDER_ID):
        await app.state.permission_client.create_resource_node(
            resource_id=folder_id, parent_id=ROOT_FOLDER_ID
        )

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
        sensors=[*http_sensor_declarations(), STRUCTURE_VIOLATIONS.as_dict()],
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
setup_tracing(app, settings)

# Sensor concept (10.1, full rollout): must run at module level, right
# after `app` is constructed - see bootstrap_http_sensors's docstring
# for why this can't move into `lifespan` (FastAPI forbids adding
# middleware once the app has started).
sensor_config_proxy, sensor_registry, _http_requests_sensor, _http_duration_sensor = (
    bootstrap_http_sensors(app, settings.service_name)
)
# Filing-structure violations (P91-S2a, ADR 0255): counted in "report" mode
# (allowed through) and "enforce" mode (refused), so a rollout can be
# watched before switching to "enforce".
STRUCTURE_VIOLATIONS = SensorSpec(
    name="folder.structure.violations",
    group="reliability",
    cost=1,
    description="Verstöße gegen die Ablagestruktur (Ordner), nach Modus",
)
_structure_violations = sensor_registry.counter(STRUCTURE_VIOLATIONS, labelnames=("mode",))


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
    session: AsyncSession,
    name: str,
    attributes: dict,
    parent_object_type_id: int | None = None,
    parent_is_root: bool = False,
    x_dms_principal: str = "",
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

    # Reference-existence check (P64-S1, 4.5) - `object_type_client.
    # validate()` above only checks the VALUE's shape (non-empty string,
    # via `dms_constraint_engine`, which stays DB/HTTP-free per ADR 0003);
    # existence against `reference_target` ("document"/"folder") is this
    # caller's own responsibility, symmetric to `document_service.main.
    # _check_reference_attributes`.
    object_type = await app.state.object_type_client.get(object_type_id)
    reference_errors: list[str] = []
    for attribute in (object_type or {}).get("attributes") or []:
        if attribute.get("type") != "reference" or attribute.get("reference_target") is None:
            continue
        attr_name = attribute.get("name")
        value = attributes.get(attr_name)
        if not value:
            continue
        target = attribute["reference_target"]
        if target == "document":
            exists = await app.state.document_client.get(value, x_dms_principal=x_dms_principal)
            exists = exists is not None
        else:
            exists = await repository.folder_exists(session, value)
        if not exists:
            reference_errors.append(
                f"Attribut {attr_name!r} referenziert {target} {value!r}, das nicht existiert"
            )
    if reference_errors:
        raise HTTPException(status_code=422, detail={"errors": reference_errors})


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


# Teamspace roots are areas, not filing structure (ADR 0255, Decision 2):
# teamspace-service creates them directly under the root.
_STRUCTURE_EXEMPT_ROOT_CREATORS = frozenset({"teamspace-service"})


async def _check_structure(
    *,
    parent_object_type_id: int | None,
    parent_is_root: bool,
    child_object_type_id: int | None,
    via_template: bool = False,
    context: str,
) -> int | None:
    """Parent-side filing-structure check for one new or moved folder (P91-S2a,
    ADR 0255). "enforce" refuses with 422, "report" logs and lets it through,
    "off" checks nothing. Returns the parent's default folder class when the
    folder is untyped and the default applies (the caller then creates it
    typed)."""
    result = await app.state.object_type_client.check_structure(
        parent_object_type_id=parent_object_type_id,
        parent_is_root=parent_is_root,
        child_kind="folder",
        child_object_type_id=child_object_type_id,
        via_template=via_template,
    )
    errors = result["errors"]
    mode = result["enforcement"]
    if errors:
        _structure_violations.inc(mode=mode)
        if mode == "enforce":
            raise HTTPException(status_code=422, detail={"errors": errors})
        logger.warning("structure_violation (%s, %s mode): %s", context, mode, "; ".join(errors))
        return None
    return result.get("default_object_type_id")


@app.post("/folders", response_model=FolderOut, status_code=status.HTTP_201_CREATED)
async def create_folder(
    payload: FolderCreate,
    x_dms_principal: str = Header(default=""),
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
) -> FolderOut:
    actor_created_by = identity.actor
    try:
        parent_folder = await repository.get_folder(session, payload.parent_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_folder_permission(x_dms_principal, payload.parent_id, access_type="write")

    # Filing structure (P91-S2a, ADR 0255). A defaulted folder skips the
    # attribute checks below, like a template-applied one (ADR 0056): its
    # required attributes are filled in afterwards.
    defaulted = False
    is_exempt_area_root = (
        payload.parent_id == ROOT_FOLDER_ID
        and identity.principal in _STRUCTURE_EXEMPT_ROOT_CREATORS
    )
    if not is_exempt_area_root:
        default_type_id = await _check_structure(
            parent_object_type_id=parent_folder.object_type_id,
            parent_is_root=payload.parent_id == ROOT_FOLDER_ID,
            child_object_type_id=payload.object_type_id,
            context=f"create {payload.name!r}",
        )
        if payload.object_type_id is None and default_type_id is not None:
            payload.object_type_id = default_type_id
            defaulted = True

    if not defaulted:
        await _validate_against_object_type(
            payload.object_type_id,
            session=session,
            name=payload.name,
            attributes=payload.attributes,
            parent_object_type_id=parent_folder.object_type_id,
            parent_is_root=payload.parent_id == ROOT_FOLDER_ID,
            x_dms_principal=x_dms_principal,
        )
    try:
        folder = await repository.create_folder(
            session,
            name=payload.name,
            parent_id=payload.parent_id,
            object_type_id=payload.object_type_id,
            attributes=payload.attributes,
            created_by=actor_created_by,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if is_exempt_area_root:
        folder.structure_exempt = True

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
        actor=actor_created_by,
    )
    return folder


@app.get("/folders/deleted", response_model=list[FolderOut])
async def list_deleted_folders(
    parent_id: str | None = None,
    scope: str | None = None,
    x_dms_principal: str = Header(default=""),
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
    administration - since Post-Roadmap Phase 39 Session 1, ADR 0150,
    gated by `_require_deletion_permission` instead of the legacy
    `X-DMS-Roles` string check) - no `admin_classified` variant as in
    document-service, concept 2.5 marks only documents as classified
    documents, not folders."""
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
        await _require_deletion_permission(x_dms_principal)
        return await repository.list_deleted_folders(session, parent_id=parent_id)

    raise HTTPException(status_code=422, detail=f"Unbekannter scope {scope!r}")


@app.post("/folders/{folder_id}/purge", status_code=status.HTTP_204_NO_CONTENT)
async def purge_folder(
    folder_id: str,
    x_dms_principal: str = Header(default=""),
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
    once the subtree no longer contains any active subfolders/documents.
    Since Post-Roadmap Phase 39 Session 1 (ADR 0150), gated by
    `_require_deletion_permission` (`admin.deletion`) instead of the legacy
    `trash_hard_delete_admin_role` `X-DMS-Roles` string check."""
    await _require_deletion_permission(x_dms_principal)
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
    # Phase 44 Session 2/ADR 0163: see `_execute_or_defer_forced_deletion`'s
    # identical fix - same missing structure-tree event.
    await publish_event(
        "folder.resource.deleted",
        subject=folder_id,
        payload={"resource_id": folder_id},
        actor=x_dms_principal,
    )


@app.get("/folders/{folder_id}", response_model=FolderOut)
async def get_folder(
    folder_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> FolderOut:
    try:
        folder = await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_folder_permission(x_dms_principal, folder_id, access_type="read")
    # Forensic trace (5.4b, P71-S2) - only on the single retrieval, never on
    # `GET /folders/{id}/children` (mirrors `document_service.get_document`'s
    # own `document.viewed`, same "avoid dozens of events per listing"
    # reasoning documented there).
    config = await repository.get_audit_trace_config(session)
    if config.log_viewed:
        await publish_event("folder.viewed", folder_id, {}, actor=x_dms_principal or None)
    await session.commit()
    return folder


HIDDEN_COUNT_HEADER = "X-DMS-Hidden-Count"
# Breadcrumb placeholders carry no real metadata, not even timestamps.
_PLACEHOLDER_TIME = datetime(1970, 1, 1, tzinfo=UTC)


async def _readable_ids(x_dms_principal: str, ids: list[str]) -> set[str]:
    """The folders among `ids` the caller may read, in one batch check."""
    if not ids:
        return set()
    allowed = await app.state.permission_client.check_batch(
        principal_id=x_dms_principal,
        resource_ids=ids,
        permission="folder.read",
        access_type="read",
    )
    return {folder_id for folder_id in ids if allowed.get(folder_id, False)}


@app.get("/folders/{folder_id}/children", response_model=list[FolderOut])
async def list_children(
    folder_id: str,
    response: Response,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[FolderOut]:
    """Only the children the caller may read (P91-S4, ADR 0259): a folder
    with its own permissions (a teamspace root, `inherit=False`) no longer
    shows up - neither its name nor that it exists - for someone who can't
    open it. One `check_batch` per listing. A service principal additionally
    gets `X-DMS-Hidden-Count`, so a migration can refuse to skip silently;
    users don't, since even a count tells them something exists."""
    try:
        children = await repository.list_children(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_folder_permission(x_dms_principal, folder_id, access_type="read")
    readable = await _readable_ids(x_dms_principal, [child.id for child in children])
    visible = [child for child in children if child.id in readable]
    if x_dms_principal in SERVICE_PRINCIPALS:
        response.headers[HIDDEN_COUNT_HEADER] = str(len(children) - len(visible))
    return visible


@app.get("/folders/{folder_id}/breadcrumb", response_model=list[FolderOut])
async def get_breadcrumb(
    folder_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[FolderOut]:
    """Ancestor chain from `root` to `folder_id`, root-first (Phase 79
    Session 1) - closes ADR 0015's own long-standing "no endpoint for
    breadcrumb/full path" gap, see `repository.get_breadcrumb`. Gated like
    `GET /folders/{folder_id}` itself (`folder.read` on the target, same
    reasoning as `list_children` above).

    Since P91-S4 (ADR 0259): an ancestor the caller can't read (a folder
    below a teamspace root, reached through a favorite or a link) comes
    back as a placeholder - id and position only, `restricted: true`, no
    name or content. Hiding it would make the path look shorter than it is,
    and the readable folder's place already implies an ancestor exists."""
    try:
        chain = await repository.get_breadcrumb(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_folder_permission(x_dms_principal, folder_id, access_type="read")
    ancestors = [folder.id for folder in chain if folder.id != folder_id]
    readable = await _readable_ids(x_dms_principal, ancestors) | {folder_id}
    return [
        FolderOut.model_validate(folder)
        if folder.id in readable
        else FolderOut(
            id=folder.id,
            name="",
            parent_id=folder.parent_id,
            object_type_id=None,
            attributes={},
            deleted_at=None,
            deleted_by=None,
            retention_until=None,
            full_deletion=False,
            retention_pseudonymize=False,
            pending_deletion_reason=None,
            reminder_notify_email=None,
            created_by="",
            created_at=_PLACEHOLDER_TIME,
            updated_at=_PLACEHOLDER_TIME,
            restricted=True,
        )
        for folder in chain
    ]


@app.patch("/folders/{folder_id}", response_model=FolderOut)
async def update_folder(
    folder_id: str,
    payload: FolderUpdate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> FolderOut:
    try:
        current = await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_folder_permission(x_dms_principal, folder_id, access_type="write")

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
    if is_move:
        # Filing structure (P91-S2a): the moved folder against its new
        # parent. Its descendants keep their relation to it, so they aren't
        # re-checked.
        try:
            target_parent = await repository.get_folder(session, payload.parent_id)
        except repository.NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await _check_structure(
            parent_object_type_id=target_parent.object_type_id,
            parent_is_root=payload.parent_id == ROOT_FOLDER_ID,
            child_object_type_id=current.object_type_id,
            context=f"move {folder_id}",
        )
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
            session=session,
            name=payload.name if payload.name is not None else current.name,
            attributes=payload.attributes if payload.attributes is not None else current.attributes,
            x_dms_principal=x_dms_principal,
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
        await publish_event(
            "folder.resource.moved",
            subject=folder.id,
            payload={"resource_id": folder.id, "new_parent_id": folder.parent_id},
            actor=x_dms_principal,
        )
    # P90-S4 (ADR 0256): a rename or attribute change published nothing, so
    # nobody watching the folder (and no audit consumer) could see it.
    changed = [
        field
        for field, value in (("name", payload.name), ("attributes", payload.attributes))
        if value is not None
    ]
    if changed:
        await publish_event(
            "folder.updated",
            subject=folder.id,
            payload={"name": folder.name, "changed_fields": changed},
            actor=x_dms_principal,
        )
    return folder


@app.delete("/folders/{folder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder(
    folder_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Immediate hard delete - remains as a fallback for already-empty
    cases that never had retention applied. The regular path since P7-S1b
    is `POST /folders/{folder_id}/trash`."""
    if folder_id in PROTECTED_FOLDER_IDS:
        raise HTTPException(
            status_code=409, detail=f"Sonderordner {folder_id!r} kann nicht gelöscht werden"
        )
    await _require_folder_delete_permission(x_dms_principal, folder_id)
    # Phase 51 Session 3: `repository.delete_folder()` only ever checked for
    # contained SUBFOLDERS (`list_children`, a local DB query) - documents
    # live in a different service and were never checked at all on this
    # legacy hard-delete fallback path, a real orphaning risk (the regular
    # trash-based path, `POST .../trash` below, is unaffected - it cascades
    # onto documents via `document_client.cascade_trash`). Same
    # cross-service check the forced-deletion path already uses
    # (`document_client.count_active`), same `FolderNotEmptyError`/`409` the
    # subfolder check below already raises - "cannot delete a non-empty
    # folder" is one caller-facing contract regardless of which kind of
    # child blocks it.
    if await app.state.document_client.count_active([folder_id]) > 0:
        raise HTTPException(
            status_code=409, detail=f"Ordner {folder_id!r} enthält noch aktive Dokumente"
        )
    try:
        await repository.delete_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.FolderNotEmptyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "folder.resource.deleted",
        subject=folder_id,
        payload={"resource_id": folder_id},
        actor=x_dms_principal,
    )


@app.post("/folders/{folder_id}/trash", response_model=TrashResult)
async def trash_folder(
    folder_id: str,
    payload: TrashRequest,
    x_dms_principal: str = Header(default=""),
    x_dms_maintenance_active: str = Header(default="false"),
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
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
    actor_deleted_by = identity.actor
    if folder_id in PROTECTED_FOLDER_IDS:
        raise HTTPException(
            status_code=409,
            detail=f"Sonderordner {folder_id!r} kann nicht in den Papierkorb verschoben werden",
        )
    await _require_folder_delete_permission(x_dms_principal, folder_id)
    # Maintenance mode (4.8), Category A (Phase 51 Session 4, ADR 0152):
    # `soft_delete_folder` below cascades into document-service
    # (`POST /documents/cascade-trash`) - the same kind of write an
    # emergency lockdown exists to stop, previously unchecked here.
    await _reject_during_maintenance(x_dms_maintenance_active)
    if await app.state.approval_client.requires_approval("folder.delete"):
        request = await app.state.approval_client.create_request(
            action_type="folder.delete",
            initiated_by=actor_deleted_by,
            payload={"folder_id": folder_id, "deleted_by": actor_deleted_by},
        )
        return TrashResult(status="pending_approval", approval_request_id=request["id"])

    try:
        folder = await repository.soft_delete_folder(
            session,
            folder_id,
            deleted_by=actor_deleted_by,
            document_client=app.state.document_client,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "folder.trashed",
        subject=folder_id,
        payload={"deleted_by": actor_deleted_by},
        actor=actor_deleted_by,
    )
    return TrashResult(status="trashed", folder=folder)


@app.post("/folders/{folder_id}/restore", response_model=FolderOut)
async def restore_folder(
    folder_id: str,
    x_dms_principal: str = Header(default=""),
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> FolderOut:
    """Trash restore (5.2, since P7-S1b) - only possible within the
    configured retention period, also restores subfolders/documents that
    were deleted via cascade."""
    # Existence check before permission (404 before 403) - `get_folder_
    # any_state` (not the trash-filtered `get_folder`, since this must also
    # find an already-trashed folder) instead of leaving `does-not-exist`
    # to fall through to a 403 from an unregistered `ResourceNode`.
    try:
        await repository.get_folder_any_state(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_folder_permission(x_dms_principal, folder_id, access_type="write")
    # Maintenance mode (4.8), Category A (Phase 51 Session 4, ADR 0152): same
    # reasoning as `trash_folder` above - `restore_folder` below cascades
    # into document-service (`POST /documents/cascade-restore`).
    await _reject_during_maintenance(x_dms_maintenance_active)
    try:
        folder = await repository.restore_folder(
            session,
            folder_id,
            document_client=app.state.document_client,
            restored_by=x_dms_principal,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.NotDeletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except repository.RestorePeriodExpiredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event("folder.restored", subject=folder_id, payload={}, actor=x_dms_principal)
    return folder


async def _require_folder_config_permission(x_dms_principal: str) -> None:
    """RBAC (P71-S2) - `audit-trace-config` (below) is this service's first
    installation-wide settings page with no natural existing capability to
    reuse: `admin.retention` covers retention/disposal policy specifically
    (a materially different concern), not audit-trace depth. New
    capability `admin.folder_config` (role "domain-admin-folder-config"),
    mirroring `document-service`'s own `admin.document_config` - "one
    capability per owning service for its own settings pages" (same
    precedent, ADR 0148)."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    if not await app.state.permission_client.has_permission(x_dms_principal, "admin.folder_config"):
        raise HTTPException(
            status_code=403,
            detail="Fehlende Domain-Admin-Rolle 'Ordnerdienst-Konfiguration'",
        )


async def _require_retention_permission(x_dms_principal: str) -> None:
    """RBAC (Post-Roadmap Phase 38 Session 3) - mirrors `document-service`'s
    identical fix (see there for the full rationale): scheduling/changing a
    folder's retention deadline/forced-deletion flag, and the two
    installation-wide `retention-config`/`trash-config` settings below,
    previously had no permission check at all. New capability
    `admin.retention` (role "domain-admin-retention"), shared with
    `document-service`'s own identical endpoints - one capability across
    both services, since it's the same retention/disposal-policy concern
    for both document and folder resources, not two separate domains."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    if not await app.state.permission_client.has_permission(x_dms_principal, "admin.retention"):
        raise HTTPException(
            status_code=403,
            detail="Fehlende Domain-Admin-Rolle 'Aufbewahrungsverwaltung'",
        )


@app.put("/folders/{folder_id}/retention", response_model=FolderOut)
async def put_retention(
    folder_id: str,
    payload: RetentionUpdate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> FolderOut:
    """Schedule retention/forced deletion (5.2/5.2a, since P7-S1b) - actual
    enforcement happens asynchronously via `_retention_poll_loop`. Gated by
    `admin.retention` since Post-Roadmap Phase 38 Session 3, see
    `_require_retention_permission`."""
    await _require_retention_permission(x_dms_principal)
    try:
        folder = await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if payload.full_deletion and payload.retention_pseudonymize:
        raise HTTPException(
            status_code=422,
            detail=(
                "full_deletion und retention_pseudonymize schließen sich gegenseitig aus - "
                "nur eine Aktion kann bei Fristablauf ausgelöst werden"
            ),
        )
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
        retention_pseudonymize=payload.retention_pseudonymize,
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
            "retention_pseudonymize": payload.retention_pseudonymize,
        },
    )
    return updated


def _get_pseudonymization_key() -> bytes:
    """Mirrors `document_service.main._get_pseudonymization_key` (5.2,
    Phase 58 Session 1) - deliberately no fallback to a randomly generated
    key, same rationale."""
    if not settings.attribute_pseudonymization_key:
        raise HTTPException(
            status_code=503,
            detail=(
                "Kein Verschlüsselungsschlüssel für Attribut-Pseudonymisierung "
                "konfiguriert (DMS_ATTRIBUTE_PSEUDONYMIZATION_KEY)"
            ),
        )
    return base64.b64decode(settings.attribute_pseudonymization_key)


async def _require_pseudonymization_permission(x_dms_principal: str) -> None:
    """Reuses the same global domain-admin capability document-service's
    identically-named endpoint already established (`admin.attribute_
    pseudonymization`) - no `permission-service` change needed, same
    reuse-across-services precedent as `admin.retention`/`admin.legal_hold`
    above."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    if not await app.state.permission_client.has_permission(
        x_dms_principal, "admin.attribute_pseudonymization"
    ):
        raise HTTPException(
            status_code=403,
            detail="Fehlende Domain-Admin-Rolle 'Attribut-Pseudonymisierung'",
        )


async def _require_reveal_permission(x_dms_principal: str) -> None:
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    if not await app.state.permission_client.has_permission(
        x_dms_principal, "admin.attribute_reveal"
    ):
        raise HTTPException(
            status_code=403,
            detail="Fehlende Domain-Admin-Rolle 'Pseudonymisierte Attribute aufdecken'",
        )


@app.post(
    "/folders/{folder_id}/attributes/{attribute_name}/pseudonymize",
    response_model=PseudonymizedAttributeOut,
    status_code=status.HTTP_201_CREATED,
)
async def pseudonymize_folder_attribute(
    folder_id: str,
    attribute_name: str,
    payload: PseudonymizeAttributeRequest,
    x_dms_principal: str = Header(default=""),
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
) -> PseudonymizedAttributeOut:
    """Mirrors `document_service.main.pseudonymize_document_attribute`
    exactly (5.2, Phase 58 Session 1, ADR 0156's own named Open Point)."""
    actor_pseudonymized_by = identity.actor
    await _require_pseudonymization_permission(x_dms_principal)
    try:
        folder = await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if folder.object_type_id is None:
        raise HTTPException(
            status_code=400,
            detail="Ordner hat keinen Objekttyp - kein Attributschema bekannt",
        )
    object_type = await app.state.object_type_client.get(folder.object_type_id)
    if object_type is None:
        raise HTTPException(status_code=400, detail="Objekttyp des Ordners nicht gefunden")
    attribute_definition = next(
        (a for a in object_type["attributes"] if a.get("name") == attribute_name), None
    )
    if attribute_definition is None or not attribute_definition.get("personal_data"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Attribut {attribute_name!r} ist im Objekttyp nicht als "
                "personenbezogen markiert (personal_data)"
            ),
        )

    value = folder.attributes.get(attribute_name)
    if value is None or value == "":
        raise HTTPException(status_code=400, detail=f"Attribut {attribute_name!r} hat keinen Wert")

    key = _get_pseudonymization_key()
    encrypted_value = crypto.encrypt(json.dumps(value).encode("utf-8"), key)

    try:
        vault_entry = await repository.pseudonymize_attribute(
            session,
            folder_id,
            attribute_name,
            encrypted_value=encrypted_value,
            pseudonymized_by=actor_pseudonymized_by,
            reason=payload.reason,
        )
    except repository.AlreadyPseudonymizedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "folder.attribute.pseudonymized",
        subject=folder_id,
        payload={"attribute_name": attribute_name},
        actor=actor_pseudonymized_by,
    )
    return vault_entry


async def _pseudonymize_eligible_attributes(
    session: AsyncSession,
    folder: Folder,
    object_type_client: ObjectTypeClient,
    *,
    triggered_by: str,
    reason: str,
) -> list[str]:
    """Automatic retention-expiry trigger (5.2, Phase 58 Session 1) -
    mirrors `document_service.main._pseudonymize_eligible_attributes`
    exactly, including taking `object_type_client` as an explicit
    parameter rather than reaching into `app.state` (see that function's
    docstring for why)."""
    if folder.object_type_id is None:
        return []
    object_type = await object_type_client.get(folder.object_type_id)
    if object_type is None:
        return []
    key = _get_pseudonymization_key()
    pseudonymized: list[str] = []
    for attribute_definition in object_type["attributes"]:
        if not attribute_definition.get("personal_data"):
            continue
        attribute_name = attribute_definition["name"]
        value = folder.attributes.get(attribute_name)
        if value is None or value == "" or value == repository.PSEUDONYMIZED_ATTRIBUTE_PLACEHOLDER:
            continue
        encrypted_value = crypto.encrypt(json.dumps(value).encode("utf-8"), key)
        try:
            await repository.pseudonymize_attribute(
                session,
                folder.id,
                attribute_name,
                encrypted_value=encrypted_value,
                pseudonymized_by=triggered_by,
                reason=reason,
            )
        except repository.AlreadyPseudonymizedError:
            continue
        pseudonymized.append(attribute_name)
    return pseudonymized


@app.post(
    "/folders/{folder_id}/attributes/{attribute_name}/reveal",
    response_model=RevealedAttributeOut,
)
async def reveal_folder_attribute(
    folder_id: str,
    attribute_name: str,
    payload: RevealAttributeRequest,
    x_dms_principal: str = Header(default=""),
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
) -> RevealedAttributeOut:
    """Mirrors `document_service.main.reveal_document_attribute` exactly
    (5.2, Phase 58 Session 1)."""
    actor_revealed_by = identity.actor
    await _require_reveal_permission(x_dms_principal)
    try:
        vault_entry = await repository.get_pseudonymized_attribute(
            session, folder_id, attribute_name
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    key = _get_pseudonymization_key()
    try:
        original_value = json.loads(crypto.decrypt(vault_entry.encrypted_value, key))
    except crypto.DecryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await repository.mark_revealed(session, vault_entry.id, revealed_by=actor_revealed_by)
    await session.commit()
    await publish_event(
        "folder.attribute.revealed",
        subject=folder_id,
        payload={"attribute_name": attribute_name},
        actor=actor_revealed_by,
    )
    return RevealedAttributeOut(
        folder_id=folder_id,
        attribute_name=attribute_name,
        value=original_value,
        reason=vault_entry.reason,
        pseudonymized_by=vault_entry.pseudonymized_by,
        pseudonymized_at=vault_entry.pseudonymized_at,
    )


@app.get(
    "/folders/{folder_id}/attributes/pseudonymized",
    response_model=list[PseudonymizedAttributeOut],
)
async def list_pseudonymized_folder_attributes(
    folder_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[PseudonymizedAttributeOut]:
    """Mirrors `document_service.main.list_pseudonymized_document_
    attributes` - gated like any other regular folder read (`folder.read`),
    NOT the admin-only reveal capability (5.2, Phase 58 Session 1)."""
    try:
        await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_folder_permission(x_dms_principal, folder_id, access_type="read")
    return await repository.list_pseudonymized_attributes(session, folder_id)


async def _reject_during_maintenance(x_dms_maintenance_active: str) -> None:
    """Maintenance mode (4.8), Category A request-triggered cascading writes
    (Phase 51 Session 4, ADR 0152) - same helper shape and message as
    `workflow-service`'s own `_reject_during_maintenance` (P6-S6), reading
    the header the gateway already forwards on the inbound request rather
    than an extra `permission-service` round trip (`is_maintenance_active()`
    remains for this service's own Category B poll loop above, which has no
    inbound request to read a header off). Guards `trash_folder`/
    `restore_folder`, the two endpoints here that cascade into
    document-service's own write path."""
    if x_dms_maintenance_active.lower() == "true":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Systemweite Notfallsperre aktiv - Wartungsmodus",
        )


async def _require_folder_permission(
    x_dms_principal: str, resource_id: str, *, access_type: str
) -> None:
    """RBAC (Post-Roadmap Phase 38 Session 4, ADR 0149) - core folder CRUD
    (`POST`/`GET`/`PATCH`/`DELETE /folders/...`, trash/restore/purge,
    folder templates) previously had NO permission check at all (see
    `_require_folder_document_reference_permission` below for the one
    prior exception, hand folders/ADR 0118 - that helper predates this one
    but deliberately checks a DIFFERENT, dedicated permission pair, see its
    own docstring for why). Checks `folder.read`/`folder.write` against the
    resource tree, granted to "everyone" (preserving default openness for
    ordinary, non-teamspace folders) while a teamspace's root folder
    deliberately does NOT inherit that grant (`inherit=False`, see
    `docs/adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md`)
    - only its own members' existing per-member role assignment (ADR 0043)
    applies there. `401` without a principal header, `403` without the
    permission; callers resolve `404` (unknown resource) themselves first,
    same ordering as the hand-folder helper."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    permission = "folder.read" if access_type == "read" else "folder.write"
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=resource_id,
        permission=permission,
        access_type=access_type,
    )
    if not allowed:
        raise HTTPException(
            status_code=403, detail=f"Fehlende Berechtigung {permission!r} auf {resource_id!r}"
        )


async def _require_folder_delete_permission(x_dms_principal: str, folder_id: str) -> None:
    """Deliberately its OWN permission, `folder.delete`, NOT the generic
    `folder.write` `_require_folder_permission` above checks (Post-Roadmap
    Phase 44 Session 2, ADR 0163) - same "dedicated permission pair" shape
    as `_require_folder_document_reference_permission`'s existing
    precedent, applied here for a different reason: `folder.write` is
    granted broadly (to "everyone" for ordinary folders, and to every
    `teamspace-member` - not just managers - on a teamspace's root folder,
    ADR 0149) because ordinary write actions (create/rename/move/restore)
    are meant to be that open. Deleting/trashing a folder is not the same
    kind of action - in particular, ANY teamspace member could otherwise
    destroy the entire teamspace by calling `DELETE /folders/{root_folder_
    id}` or `POST /folders/{root_folder_id}/trash` directly, bypassing
    `teamspace-service`'s own manager-only guard on `DELETE /teamspaces/
    {id}` entirely (that guard only ever protected the teamspace metadata
    row, never the underlying folder). `folder.delete` is granted to
    "everyone" (preserving today's default-open behavior for ordinary,
    non-teamspace folders - this is a narrowing for teamspaces specifically,
    not a new restriction project-wide) but, on a teamspace's root folder,
    only to members with `can_manage_members=True` (`teamspace-manager`,
    see `docs/services/teamspace-service.md`)."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=folder_id,
        permission="folder.delete",
        access_type="write",
    )
    if not allowed:
        raise HTTPException(
            status_code=403, detail=f"Fehlende Berechtigung 'folder.delete' auf {folder_id!r}"
        )


async def _require_folder_document_reference_permission(
    x_dms_principal: str, folder_id: str, *, access_type: str
) -> None:
    """Hand folders (14.2, post-roadmap phase 31 session 7, ADR 0118) -
    curating a hand folder's cross-referenced compilation is gated by a
    real, folder-scoped check against permission-service's existing
    resource tree (populated from this service's own `folder.resource.*`
    events, see docs/services/permission-service.md).

    Uses `folder.document_reference.read`/`.write` - deliberately its OWN
    permission pair, NOT the generic `folder.read`/`folder.write` this
    module's core-CRUD gate (`_require_folder_permission`) checks (Post-
    Roadmap Phase 38 Session 4, ADR 0149). ADR 0118 explicitly documented
    `folder.read`/`folder.write` being absent from "everyone" as this
    feature's actual security property ("an installation must explicitly
    grant them per hand folder... before anyone besides the creator can
    curate/view one - this is the intended, deliberate behavior"). Session
    4 had to add both to "everyone" so ordinary, non-teamspace folder CRUD
    stays open by default (see `EVERYONE_ROLE_PERMISSIONS` in permission-
    service) - reusing the same two strings here would have silently
    handed every authenticated principal curation rights on every hand
    folder, defeating ADR 0118's model rather than merely inheriting its
    openness. A dedicated pair keeps that model intact: nobody has it via
    "everyone", so a hand folder is exactly as closed as before this
    session. A real installation with an existing per-hand-folder grant on
    the old `folder.read`/`folder.write` strings needs to be re-granted
    under the new names - no admin-ui surface exists for this yet (ADR
    0118's own "Consequences"), so the blast radius is limited to whoever
    set one up by hand via permission-service's API directly.

    Callers check `401`/`404` (missing principal / unknown folder)
    themselves before calling this - resolving whether the resource even
    exists has to happen before asking "does X have permission on it",
    same ordering already established by the public share-link creation
    endpoint (document-service, ADR 0047)."""
    permission = (
        "folder.document_reference.read"
        if access_type == "read"
        else "folder.document_reference.write"
    )
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=folder_id,
        permission=permission,
        access_type=access_type,
    )
    if not allowed:
        raise HTTPException(
            status_code=403, detail=f"Fehlende Berechtigung {permission!r} auf {folder_id!r}"
        )


def _resolve_document_reference(reference, document: dict | None) -> FolderDocumentReferenceOut:
    return FolderDocumentReferenceOut(
        document_id=reference.document_id,
        added_by=reference.added_by,
        added_at=reference.added_at,
        removed_by=reference.removed_by,
        removed_at=reference.removed_at,
        current_version_number=document["current_version_number"] if document else None,
        document_deleted_at=document["deleted_at"] if document else None,
    )


@app.post(
    "/folders/{folder_id}/document-references",
    response_model=FolderDocumentReferenceOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_folder_document_reference(
    folder_id: str,
    payload: FolderDocumentReferenceAdd,
    x_dms_principal: str = Header(default=""),
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
) -> FolderDocumentReferenceOut:
    actor_added_by = identity.actor
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    try:
        await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_folder_document_reference_permission(
        x_dms_principal, folder_id, access_type="write"
    )
    document = await app.state.document_client.get(
        payload.document_id, x_dms_principal=x_dms_principal
    )
    if document is None:
        raise HTTPException(
            status_code=400, detail=f"document_id {payload.document_id!r} unbekannt"
        )
    reference = await repository.add_document_reference(
        session, folder_id, document_id=payload.document_id, added_by=actor_added_by
    )
    await session.commit()
    await publish_event(
        "folder.document_reference.added",
        subject=folder_id,
        payload={
            "document_id": payload.document_id,
            "added_by": actor_added_by,
            # Post-Roadmap Phase 35 Session 4 (ADR 0146): search-service's
            # new cross-folder index needs the real `added_at`, not an
            # approximation from when it happened to observe the event -
            # this endpoint is the only place that knows it, since the
            # reference-listing endpoint that WOULD tell a consumer this
            # otherwise is itself `folder.read`-gated (ADR 0118), unlike
            # every other resource this service lets search-service read
            # freely (e.g. `GET /folders/{id}` for folder_name).
            "added_at": reference.added_at.isoformat(),
        },
        actor=actor_added_by,
    )
    return _resolve_document_reference(reference, document)


@app.delete(
    "/folders/{folder_id}/document-references/{document_id}",
    response_model=FolderDocumentReferenceOut,
)
async def remove_folder_document_reference(
    folder_id: str,
    document_id: str,
    payload: FolderDocumentReferenceRemove,
    x_dms_principal: str = Header(default=""),
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
) -> FolderDocumentReferenceOut:
    actor_removed_by = identity.actor
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    try:
        await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_folder_document_reference_permission(
        x_dms_principal, folder_id, access_type="write"
    )
    try:
        reference = await repository.remove_document_reference(
            session, folder_id, document_id, removed_by=actor_removed_by
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "folder.document_reference.removed",
        subject=folder_id,
        payload={"document_id": document_id, "removed_by": actor_removed_by},
        actor=actor_removed_by,
    )
    document = await app.state.document_client.get(document_id, x_dms_principal=x_dms_principal)
    return _resolve_document_reference(reference, document)


@app.get(
    "/folders/{folder_id}/document-references", response_model=list[FolderDocumentReferenceOut]
)
async def list_folder_document_references(
    folder_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[FolderDocumentReferenceOut]:
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    try:
        await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_folder_document_reference_permission(
        x_dms_principal, folder_id, access_type="read"
    )
    references = await repository.list_document_references(session, folder_id)
    resolved = []
    for reference in references:
        document = (
            await app.state.document_client.get(
                reference.document_id, x_dms_principal=x_dms_principal
            )
            if reference.removed_at is None
            else None
        )
        resolved.append(_resolve_document_reference(reference, document))
    return resolved


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


async def _require_deletion_permission(x_dms_principal: str) -> None:
    """RBAC (Post-Roadmap Phase 39 Session 1, ADR 0150) - trash/purge admin
    gate, migrated off the legacy `trash_hard_delete_admin_role`
    `X-DMS-Roles` string-equality check onto the real `admin.deletion`
    capability (role `domain-admin-deletion`), exact mirror of
    `document_service.main._require_deletion_permission`/its own
    `_require_classified_deletion_permission` (ADR 0133). REPLACED, not
    supplemented - same reasoning as those two migrations: a plain string
    comparison against an unverified header is not a standalone,
    conceptually anchored second gate worth defending indefinitely
    alongside a real one."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    if not await app.state.permission_client.has_permission(x_dms_principal, "admin.deletion"):
        raise HTTPException(
            status_code=403, detail="Fehlende Domain-Admin-Rolle 'Löschadministration'"
        )


@app.post("/legal-holds", response_model=LegalHoldOut, status_code=status.HTTP_201_CREATED)
async def create_legal_hold(
    payload: LegalHoldCreate,
    x_dms_principal: str = Header(default=""),
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
) -> LegalHoldOut:
    """Set a legal hold (5.2, since P7-S1b) - overrides any due action in
    the poll loop until it is released again. Since P19-S10 gated by
    `admin.legal_hold`, see `_require_legal_hold_permission`."""
    actor_set_by = identity.actor
    await _require_legal_hold_permission(x_dms_principal)
    try:
        hold = await repository.create_legal_hold(
            session, payload.folder_id, set_by=actor_set_by, reason=payload.reason
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "folder.legal_hold.set",
        subject=payload.folder_id,
        payload={"set_by": actor_set_by, "reason": payload.reason},
        actor=actor_set_by,
    )
    return hold


@app.post("/legal-holds/{hold_id}/release", response_model=LegalHoldOut)
async def release_legal_hold(
    hold_id: str,
    payload: LegalHoldReleaseRequest,
    x_dms_principal: str = Header(default=""),
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
) -> LegalHoldOut:
    actor_released_by = identity.actor
    await _require_legal_hold_permission(x_dms_principal)
    try:
        hold = await repository.release_legal_hold(session, hold_id, released_by=actor_released_by)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.AlreadyReleasedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "folder.legal_hold.released",
        subject=hold.folder_id,
        payload={"released_by": actor_released_by},
        actor=actor_released_by,
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
    # Phase 44 Session 2/ADR 0163: same missing structure-tree event as
    # every other real hard-delete call site in this module.
    await publish_event(
        "folder.resource.deleted",
        subject=folder_id,
        payload={"resource_id": folder_id},
        actor="system:restore-reconciliation",
    )


@app.get("/retention-config", response_model=RetentionConfigOut)
async def get_retention_config(session: AsyncSession = Depends(get_session)) -> RetentionConfigOut:
    config = await repository.get_retention_config(session)
    await session.commit()
    return config


@app.put("/retention-config", response_model=RetentionConfigOut)
async def put_retention_config(
    body: RetentionConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> RetentionConfigOut:
    await _require_retention_permission(x_dms_principal)
    config = await repository.update_retention_config(
        session,
        deletion_reason_required=body.deletion_reason_required,
        reminder_lead_days=body.reminder_lead_days,
        deletion_reason_catalog=body.deletion_reason_catalog,
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
    body: TrashConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> TrashConfigOut:
    await _require_retention_permission(x_dms_principal)
    config = await repository.update_trash_config(
        session, restore_period_days=body.restore_period_days
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
    body: AuditTraceConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> AuditTraceConfigOut:
    await _require_folder_config_permission(x_dms_principal)
    config = await repository.update_audit_trace_config(session, log_viewed=body.log_viewed)
    await session.commit()
    return config


# --- Filing structure: conformity, retyping, area roots (P91-S2b, ADR 0255) ---

_REPORT_ITEM_LIMIT = 500


async def _require_object_config_permission(x_dms_principal: str) -> None:
    """The structure is object-type configuration (ADR 0255): its report,
    bulk retyping and templates need the same capability as the types."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    if not await app.state.permission_client.has_permission(x_dms_principal, "admin.object_config"):
        raise HTTPException(
            status_code=403,
            detail="Fehlende Domain-Admin-Rolle 'Objekttyp-/Workflow-Konfiguration'",
        )


async def _load_structure_rules() -> tuple[dict[int, dict], dict]:
    types = await app.state.object_type_client.list_object_types()
    config = await app.state.object_type_client.get_structure_config()
    return {t["id"]: t for t in types}, config


def _folder_row(folder) -> dict:
    return {
        "id": folder.id,
        "name": folder.name,
        "parent_id": folder.parent_id,
        "object_type_id": folder.object_type_id,
        "attributes": folder.attributes or {},
        "structure_exempt": folder.structure_exempt,
    }


async def _validate_template_structure(structure: dict) -> None:
    types_by_id, _ = await _load_structure_rules()
    errors = structure_report.template_errors(structure, types_by_id)
    if errors:
        raise HTTPException(status_code=422, detail={"errors": errors})


@app.get("/structure/conformity")
async def get_structure_conformity(
    x_dms_principal: str = Header(default=""),
    limit: int = _REPORT_ITEM_LIMIT,
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Every active folder measured against the current rules (P91-S2b),
    independent of the enforcement mode - so a structure can be judged before
    it is switched on. Teamspace roots are exempt at the root."""
    await _require_object_config_permission(x_dms_principal)
    types_by_id, config = await _load_structure_rules()
    folders = [_folder_row(f) for f in await repository.list_active_folders(session)]
    report = structure_report.folder_report(
        folders, types_by_id, config, limit=max(1, min(limit, 5000))
    )
    return {"enforcement": config["enforcement"], **report}


@app.post("/folders/retype", response_model=RetypeResult)
async def retype_folders(
    payload: FolderRetypeRequest,
    x_dms_principal: str = Header(default=""),
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
) -> RetypeResult:
    """Bulk retype (P91-S2b, ADR 0255 Decision 4): each folder gets the type
    if, with it, it fits its place on both sides - whatever the enforcement
    mode, since this is the tool for reaching conformity. Required
    attributes aren't demanded (the report lists them). Children are not
    re-checked: they keep their place and show up in the report if the new
    type's lists don't admit them."""
    await _require_object_config_permission(x_dms_principal)
    types_by_id, config = await _load_structure_rules()
    new_type = types_by_id.get(payload.object_type_id) if payload.object_type_id else None
    if payload.object_type_id is not None and (
        new_type is None or new_type["applies_to"] != "folder"
    ):
        raise HTTPException(status_code=422, detail="object_type_id ist keine Ordnerklasse")
    by_id = {f["id"]: f for f in map(_folder_row, await repository.list_active_folders(session))}
    updated: list[str] = []
    failed: list[RetypeFailure] = []
    for folder_id in dict.fromkeys(payload.folder_ids):
        row = by_id.get(folder_id)
        if row is None or folder_id in PROTECTED_FOLDER_IDS:
            failed.append(RetypeFailure(id=folder_id, errors=["Ordner unbekannt oder geschützt"]))
            continue
        problems = structure_report.evaluate_folder(
            row, by_id, types_by_id, config, object_type_id=payload.object_type_id
        )
        placement = [m for p in problems if p["kind"] == "placement" for m in p["messages"]]
        if placement:
            failed.append(RetypeFailure(id=folder_id, errors=placement))
            continue
        try:
            await _require_folder_permission(x_dms_principal, folder_id, access_type="write")
        except HTTPException:
            failed.append(RetypeFailure(id=folder_id, errors=["Keine Schreibrechte"]))
            continue
        folder = await repository.get_folder(session, folder_id)
        folder.object_type_id = payload.object_type_id
        row["object_type_id"] = payload.object_type_id
        updated.append(folder_id)
    await session.commit()
    for folder_id in updated:
        await publish_event(
            "folder.updated",
            subject=folder_id,
            payload={"name": by_id[folder_id]["name"], "changed_fields": ["object_type_id"]},
            actor=identity.actor,
        )
    return RetypeResult(updated=updated, failed=failed)


@app.put("/folders/{folder_id}/structure-exempt", status_code=status.HTTP_204_NO_CONTENT)
async def mark_structure_exempt(
    folder_id: str,
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
) -> None:
    """teamspace-service confirms its root folders at startup (P91-S2b), so
    teamspaces created before the flag existed are exempt too. Only area
    owners may call it, and only for folders directly under the root."""
    if identity.principal not in _STRUCTURE_EXEMPT_ROOT_CREATORS:
        raise HTTPException(status_code=403, detail="Nur für Bereichsdienste (teamspace-service)")
    try:
        folder = await repository.get_folder(session, folder_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if folder.parent_id != ROOT_FOLDER_ID:
        raise HTTPException(status_code=409, detail="Nur Ordner direkt unter der Wurzel")
    folder.structure_exempt = True
    await session.commit()


@app.post("/folders/types")
async def get_folder_types(
    payload: FolderTypesRequest,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """`{id: object_type_id}` for many folders at once - document-service's
    conformity report evaluates documents against their folder's class
    (P91-S2b). Unknown or deleted ids are left out. Type ids only, no names
    or content, so a principal is enough."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    rows = await repository.folder_types(session, payload.ids[:20000])
    return {"types": rows}


# --- Structure templates (2.5/7.3, since P15-S6) ---


@app.post(
    "/folder-templates", response_model=FolderTemplateOut, status_code=status.HTTP_201_CREATED
)
async def create_folder_template(
    payload: FolderTemplateCreate,
    x_dms_principal: str = Header(default=""),
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
) -> FolderTemplateOut:
    """Captures the active subtree starting at `payload.source_folder_id`
    as a named, reusable structure template (2.5/7.3, e.g. a file plan
    skeleton) - see `repository.build_template_structure`/ADR 0056. Gated
    by `folder.read` on `source_folder_id` since Post-Roadmap Phase 38
    Session 4 - the captured structure (names/hierarchy) could otherwise
    leak a teamspace's internal organization to a non-member."""
    actor_created_by = identity.actor
    # Admin-managed since P91-S2b (ADR 0255, Decision 5).
    await _require_object_config_permission(x_dms_principal)
    if (payload.source_folder_id is None) == (payload.structure is None):
        raise HTTPException(
            status_code=422, detail="Genau eines von source_folder_id oder structure angeben"
        )
    if payload.source_folder_id is not None:
        try:
            structure = await repository.build_template_structure(session, payload.source_folder_id)
        except repository.NotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await _require_folder_permission(
            x_dms_principal, payload.source_folder_id, access_type="read"
        )
    else:
        structure = payload.structure.model_dump()
    await _validate_template_structure(structure)
    template = await repository.create_template(
        session,
        name=payload.name,
        description=payload.description,
        structure=structure,
        created_by=actor_created_by,
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


@app.put("/folder-templates/{template_id}", response_model=FolderTemplateDetailOut)
async def update_folder_template(
    template_id: str,
    payload: FolderTemplateUpdate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> FolderTemplateDetailOut:
    """Template editing (P91-S2b): name, description and the structure tree.
    A new structure is validated against the rules like a new template."""
    await _require_object_config_permission(x_dms_principal)
    try:
        template = await repository.get_template(session, template_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if payload.structure is not None:
        structure = payload.structure.model_dump()
        await _validate_template_structure(structure)
        template.structure = structure
    if payload.name is not None:
        template.name = payload.name
    if "description" in payload.model_fields_set:
        template.description = payload.description
    await session.commit()
    return template


@app.delete("/folder-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_folder_template(
    template_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_object_config_permission(x_dms_principal)
    try:
        await repository.delete_template(session, template_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


async def _validate_template_nesting(
    node: dict,
    object_type_client: ObjectTypeClient,
    *,
    parent_object_type_id: int | None,
    parent_is_root: bool,
    structure_errors: dict | None = None,
) -> list[str]:
    """Phase 79 Session 1 - the parent-child nesting half of ADR 0056's own
    documented "Structure templates check neither required attributes nor
    parent-child object-type nesting rules when applying" gap. Deliberately
    does NOT reuse `_validate_against_object_type` (that also enforces
    required attributes/naming, which a template node's still-empty
    `attributes={}` would spuriously fail - ADR 0056 defers those checks to
    the later `PATCH` once a real value is filled in). Only checks
    placement, via the shared `dms_constraint_engine.validate_parent` - the
    same single source of truth `object-type-service`'s own `/validate`
    uses internally, without a round trip to it (`object_type_client.get()`
    already returns `allowed_parent_types` on the same response this
    service fetches for other reasons elsewhere, e.g. pseudonymization).
    Walks the whole subtree BEFORE any folder is created (called ahead of
    `repository.apply_template`) so an invalid node deep in the tree
    rejects the entire application instead of leaving a partially-created
    subtree behind."""
    errors: list[str] = []
    object_type_id = node.get("object_type_id")
    if object_type_id is not None:
        object_type = await object_type_client.get(object_type_id)
        if object_type is not None:
            if parent_is_root:
                parent_type_name = ROOT_PARENT_TYPE
            elif parent_object_type_id is not None:
                parent_type = await object_type_client.get(parent_object_type_id)
                parent_type_name = parent_type["name"] if parent_type is not None else None
            else:
                parent_type_name = None
            node_errors = validate_parent(
                {"allowedParentTypes": object_type.get("allowed_parent_types")}, parent_type_name
            )
            if node_errors:
                errors.append(f"{node['name']!r}: " + "; ".join(node_errors))
    if structure_errors is not None:
        # Filing structure, parent side (P91-S2a): `via_template` satisfies a
        # template-only parent. A template node never takes a default: the
        # template says what it creates, so an untyped node below a list is
        # a template problem.
        result = await object_type_client.check_structure(
            parent_object_type_id=parent_object_type_id,
            parent_is_root=parent_is_root,
            child_kind="folder",
            child_object_type_id=object_type_id,
            via_template=True,
        )
        node_structure_errors = list(result["errors"])
        if object_type_id is None and result.get("default_object_type_id") is not None:
            node_structure_errors.append("Vorlagenknoten ohne Objekttyp")
        if node_structure_errors:
            structure_errors["mode"] = result["enforcement"]
            structure_errors["errors"].append(
                f"{node['name']!r}: " + "; ".join(node_structure_errors)
            )
    for child in node.get("children", []):
        errors.extend(
            await _validate_template_nesting(
                child,
                object_type_client,
                parent_object_type_id=object_type_id,
                parent_is_root=False,
                structure_errors=structure_errors,
            )
        )
    return errors


@app.post("/folder-templates/{template_id}/apply", response_model=FolderTemplateApplyResult)
async def apply_folder_template(
    template_id: str,
    payload: FolderTemplateApplyRequest,
    x_dms_principal: str = Header(default=""),
    identity: ActingIdentity = Depends(require_acting_identity),
    session: AsyncSession = Depends(get_session),
) -> FolderTemplateApplyResult:
    """Applies a structure template below `payload.target_parent_id` -
    creates real folders (see `repository.apply_template`) and publishes a
    regular `folder.resource.created` event for each of them, so that
    `permission-service`'s `ResourceNode` tree stays in sync (identical
    event to a single `POST /folders`). Gated by `folder.write` on
    `target_parent_id` since Post-Roadmap Phase 38 Session 4 - the same
    check `POST /folders` itself applies, since this creates real folders
    there just as directly."""
    actor_created_by = identity.actor
    try:
        template = await repository.get_template(session, template_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Existence check before permission (404 before 403, same ordering as
    # `create_folder`'s own `payload.parent_id` check above) - without this,
    # an unknown `target_parent_id` would 403 (no `ResourceNode` to walk)
    # before ever reaching `repository.apply_template`'s own 404.
    try:
        target_folder = await repository.get_folder(session, payload.target_parent_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_folder_permission(x_dms_principal, payload.target_parent_id, access_type="write")
    structure_errors: dict = {"mode": "off", "errors": []}
    nesting_errors = await _validate_template_nesting(
        template.structure,
        app.state.object_type_client,
        parent_object_type_id=target_folder.object_type_id,
        parent_is_root=payload.target_parent_id == ROOT_FOLDER_ID,
        structure_errors=structure_errors,
    )
    if nesting_errors:
        raise HTTPException(status_code=400, detail={"errors": nesting_errors})
    if structure_errors["errors"]:
        # Same modes as a single create (P91-S2a): the whole tree is checked
        # before anything is created, so "enforce" leaves nothing behind.
        _structure_violations.inc(mode=structure_errors["mode"])
        if structure_errors["mode"] == "enforce":
            raise HTTPException(status_code=422, detail={"errors": structure_errors["errors"]})
        logger.warning(
            "structure_violation (template %s, %s mode): %s",
            template_id,
            structure_errors["mode"],
            "; ".join(structure_errors["errors"]),
        )
    try:
        created = await repository.apply_template(
            session,
            template,
            target_parent_id=payload.target_parent_id,
            created_by=actor_created_by,
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
            actor=actor_created_by,
        )
    return FolderTemplateApplyResult(root_folder=created[0], created_count=len(created))
