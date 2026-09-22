import base64
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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

from case_service import crypto, repository, status_transitions
from case_service.consumer import start_consuming
from case_service.document_client import DocumentClient
from case_service.models import Base
from case_service.object_type_client import ObjectTypeClient
from case_service.schemas import (
    CaseArchivalConfigIn,
    CaseArchivalConfigOut,
    CaseArchiveStatusOut,
    CaseCreate,
    CaseDocumentAdd,
    CaseDocumentReferenceOut,
    CaseDocumentRemove,
    CaseNumberConfigIn,
    CaseNumberConfigOut,
    CaseOut,
    CaseRegisterRequest,
    PseudonymizeAttributeRequest,
    PseudonymizedAttributeOut,
    RevealAttributeRequest,
    RevealedAttributeOut,
)
from case_service.settings import Settings
from case_service.workflow_client import ProcessDefinitionUnknownError, WorkflowClient

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        # "case" is a reserved SQL keyword (CASE WHEN) - unlike
        # Base.metadata.create_all (which quotes automatically via
        # SQLAlchemy's IdentifierPreparer), this raw SQL string must quote
        # it itself.
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS "case"'))
        await conn.run_sync(Base.metadata.create_all)
        # Records disposal (5.6, since P7-S3b) - ad-hoc migration like
        # everywhere in this system (no Alembic), "case" must continue to
        # be quoted.
        await conn.execute(
            text('ALTER TABLE "case".cases ADD COLUMN IF NOT EXISTS archive_after TIMESTAMPTZ')
        )
        await conn.execute(
            text('ALTER TABLE "case".cases ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ')
        )
        # Case number (2.3/2.5, P15-S3) - same ad-hoc migration pattern.
        await conn.execute(
            text('ALTER TABLE "case".cases ADD COLUMN IF NOT EXISTS vorgangsnummer VARCHAR(64)')
        )
        # Draft / pre-registration lifecycle (post-roadmap phase 31 session
        # 2, ADR 0113): backfill only runs the very first time the column is
        # added - unlike `vorgangsnummer` above (deliberately never
        # backfilled), every already-existing case IS considered already
        # registered (it already has a real Vorgangsnummer or predates the
        # numbering scheme entirely, either way it's not a genuine new-style
        # draft) - but only at that one first-add moment, never again, or a
        # real still-unregistered draft's `NULL` would be overwritten on
        # every later restart.
        registered_at_exists = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = 'case' AND table_name = 'cases' "
                "AND column_name = 'registered_at'"
            )
        )
        if registered_at_exists.scalar() is None:
            await conn.execute(
                text('ALTER TABLE "case".cases ADD COLUMN registered_at TIMESTAMPTZ')
            )
            await conn.execute(text('UPDATE "case".cases SET registered_at = created_at'))
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.workflow_client = WorkflowClient(settings.workflow_service_base_url)
    app.state.document_client = DocumentClient(settings.document_service_base_url)
    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    # Producer (own stream "case", `case.created`/`.document.added`/
    # `.document.removed`/`.closed`) AND consumer
    # (`workflow.instance.completed`) - two separate client instances, same
    # convention as notification-service (see its main.py comment for the
    # rationale).
    producer = NatsEventBusClient(settings.nats_url, stream="case")
    await producer.connect()
    app.state.producer = producer

    # Backfill (Post-Roadmap Phase 35 Session 2, ADR 0144): every case
    # created BEFORE this session has no `ResourceNode` in permission-service
    # at all - an unregistered resource_id makes `_collect_effective_roles`
    # deny everything outright rather than fall back to root, so a
    # pre-existing case would otherwise become permanently inaccessible the
    # moment this session's `resource_id=case_id` checks went live. Uses the
    # same synchronous `POST /resources` call `create_case` uses (not just
    # the fire-and-forget event) so startup genuinely finishes with every
    # case registered, not "eventually, once NATS catches up". Running this
    # on every startup (not just once) is deliberately simple and
    # self-healing - `create_resource_node` is idempotent (a no-op once
    # caught up), and it also recovers a case whose ORIGINAL registration
    # was missed (e.g. permission-service was unreachable at creation time).
    async with app.state.session_factory() as backfill_session:
        for case in await repository.list_cases(backfill_session):
            await app.state.permission_client.create_resource_node(
                resource_id=case.id, parent_id="root", resource_type="case"
            )

    consumer = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer.connect()
    app.state.consumer = consumer
    await start_consuming(
        consumer,
        settings.subjects,
        app.state.session_factory,
        app.state.document_client,
        publish_event,
        app.state.object_type_client,
    )

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        sensors=http_sensor_declarations(),
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    await consumer.close()
    await producer.close()
    await app.state.workflow_client.close()
    await app.state.document_client.close()
    await app.state.object_type_client.close()
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


async def _reject_during_maintenance(x_dms_maintenance_active: str) -> None:
    """Maintenance mode (4.8), Category A request-triggered cascading writes
    (Phase 56 Session 1, ADR 0152) - same helper shape and message as
    `workflow-service`'s own `_reject_during_maintenance` (P6-S6), reading
    the header the gateway already forwards rather than an extra
    `permission-service` round trip. Guards `create_case` specifically -
    the one endpoint here that cascades into another service's write path
    (`workflow_client.start_instance`, a real `workflow-service` process
    instance) - ADR 0152's own Consequences named this exact call site as
    one of the remaining, unaddressed Category A cascades after P51-S4
    only covered `document-service`/`folder-service`."""
    if x_dms_maintenance_active.lower() == "true":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Systemweite Notfallsperre aktiv - Wartungsmodus",
        )


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
    await app.state.producer.publish(event_type, event.to_bytes())


async def _require_case_permission(
    x_dms_principal: str,
    *,
    access_type: str,
    resource_id: str = PermissionServiceClient.ROOT_RESOURCE_ID,
) -> None:
    """RBAC (post-roadmap Phase 19 Session 5, ADR 0070) - case-service
    previously had NO permission check at all. Checks `case.read`/
    `case.write` at `resource_id` (default `root`, the collection-level
    resource used by `POST`/`GET /cases` where no single case exists yet
    to check against). Since Post-Roadmap Phase 35 Session 2
    ([ADR 0144](../adr/0144-case-per-case-resource-type.md)), every
    per-case endpoint passes the case's own `case_id` instead - case-service
    now registers a real `ResourceNode` per case (`resource_type="case"`,
    `parent_id="root"`), analogous to `folder-service`'s own resource-tree
    registration, closing the gap ADR 0070/0121 both explicitly left open.
    The "everyone" group (ADR 0067) grants `case.read`/`case.write` at
    `root` by default, inherited by every case's node unless an admin
    narrows a specific case's own `RoleAssignment`s - preserves the
    previous de-facto-open behavior by default, but now makes it
    admin-editable PER CASE, not just system-wide. The first ever consumer
    of `libs/dms-permission-client` (P19-S1)."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    permission = "case.read" if access_type == "read" else "case.write"
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=resource_id,
        permission=permission,
        access_type=access_type,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Fehlende Berechtigung {permission!r}")


async def _filter_cases_by_permission(x_dms_principal: str, cases: list) -> list:
    """Row-level RBAC filtering for case listings (Post-Roadmap Phase 39
    Session 4, ADR 0154) - closes the gap named alongside it: `GET /cases`/
    `GET /cases/by-vorgangsnummer` previously checked only the collection-
    level `case.read` on `root` (`_require_case_permission`'s default
    `resource_id`), then returned every matching row unfiltered, ignoring
    that ADR 0144 already lets an admin narrow an INDIVIDUAL case's own
    `RoleAssignment`s - the same all-or-nothing gap `document-service`'s
    listing endpoints would have had before this session's broader
    per-document resource-tree retrofit. Same `check_batch`-then-filter
    shape as `query-service`/`reporting-service`/`search-service`'s own
    row-level filtering (this service already depends on
    `dms-permission-client`, so no new client method needed)."""
    if not cases:
        return cases
    allowed = await app.state.permission_client.check_batch(
        principal_id=x_dms_principal,
        permission="case.read",
        access_type="read",
        resource_ids=[case.id for case in cases],
    )
    return [case for case in cases if allowed.get(case.id, False)]


async def _get_case_or_404(session: AsyncSession, case_id: str):
    """Post-Roadmap Phase 35 Session 2 (ADR 0144) - every per-case endpoint
    must confirm the case actually EXISTS before calling
    `_require_case_permission(..., resource_id=case_id)`: a genuinely
    unknown `case_id` has no `ResourceNode` in permission-service either,
    and an unregistered resource_id denies every check outright (no roles
    at all, not even a fallback to root) - without this existence check
    first, `GET /cases/does-not-exist` would incorrectly return `403`
    instead of `404` for an otherwise fully authorized principal, exactly
    the kind of "an unauthorized-*looking* response for what is actually a
    missing resource" outcome this project's existing tests (rightly)
    reject."""
    try:
        return await repository.get_case(session, case_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _resolve_reference(session: AsyncSession, case, reference) -> CaseDocumentReferenceOut:
    """Two-stage reference model (2.3): while the circulation folder is
    open, the current main version is read live from the Document Service -
    from closure onward, only the fixed closure snapshot counts, without
    any further document-service call. `has_active_quarantine` (14.2, ADR
    0116, since Post-Roadmap Phase 36 Session 2) is a THIRD, independent
    resolution - always live, regardless of `case.status`/`removed_at`,
    since quarantine is a visibility flag that can change at any time, not
    part of the closure-snapshot concept above. No new case-level
    quarantine mechanism exists or is planned (case-service has no
    destruction-scheduling primitive to hook one into, same conclusion ADR
    0115/0118 already reached for redaction/hand-folders) - this only
    surfaces the document's own already-existing status."""
    current_version_number = None
    document_deleted_at = None
    if case.status == "open" and reference.removed_at is None:
        document = await app.state.document_client.get(reference.document_id)
        if document is not None:
            current_version_number = document["current_version_number"]
            document_deleted_at = document["deleted_at"]
    has_active_quarantine = await app.state.document_client.has_active_quarantine(
        reference.document_id
    )
    return CaseDocumentReferenceOut(
        document_id=reference.document_id,
        added_by=reference.added_by,
        added_at=reference.added_at,
        removed_by=reference.removed_by,
        removed_at=reference.removed_at,
        snapshot_version_number=reference.snapshot_version_number,
        current_version_number=current_version_number,
        document_deleted_at=document_deleted_at,
        has_active_quarantine=has_active_quarantine,
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


@app.post("/cases", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
async def create_case(
    payload: CaseCreate,
    x_dms_principal: str = Header(default=""),
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> CaseOut:
    await _reject_during_maintenance(x_dms_maintenance_active)
    await _require_case_permission(x_dms_principal, access_type="write")
    if payload.object_type_id is not None:
        errors = await app.state.object_type_client.validate(
            payload.object_type_id, name=payload.name, attributes=payload.attributes
        )
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})

    case_id = str(uuid.uuid4())

    # Case number (2.3/2.5, P15-S3): starting with this session, every new
    # circulation folder gets a server-generated, installation-wide unique
    # reference (basis for automatically matching incoming mail via the new
    # mail-connector). Draft / pre-registration lifecycle (post-roadmap
    # phase 31 session 2, ADR 0113): skipped entirely when `draft=True` -
    # the case instead stays unregistered until `POST .../register`.
    vorgangsnummer = None if payload.draft else await repository.next_vorgangsnummer(session)

    # P68-S1 (incidental fix, found via this session's own full regression
    # run - a real, previously-undetected P66-S2 regression, not part of
    # that session's own scope): the case row must exist and be COMMITTED
    # before workflow-service is asked to start an instance with
    # `business_key=case_id` - since P66-S2, `POST /instances` validates a
    # non-`None` `business_key` by resolving it against case-service's own
    # `GET /cases/{id}`, and a business_key that resolves to nothing is now
    # rejected with `422`. The previous order (start the instance first,
    # create the case row after) meant that lookup always failed, since the
    # case genuinely didn't exist yet on case-service's own side at that
    # moment - breaking case creation for every process definition
    # entirely, caught only by this session's first full,
    # unfiltered `scripts/run-tests.sh` run since P66-S2 shipped (prior
    # sessions only re-ran workflow-service's own test suite, which never
    # exercises this real cross-service integration path).
    # `process_instance_id` is already nullable for exactly this reason -
    # filled in below once the instance actually exists. On a genuinely
    # unknown `process_definition_id`, the just-created row is removed
    # again via `delete_unstarted_case` before returning `400` - it was
    # never returned to any caller, so this preserves the original "nothing
    # persisted on a 400" contract.
    case = await repository.create_case(
        session,
        case_id=case_id,
        name=payload.name,
        object_type_id=payload.object_type_id,
        attributes=payload.attributes,
        process_definition_id=payload.process_definition_id,
        process_instance_id=None,
        created_by=payload.created_by,
        vorgangsnummer=vorgangsnummer,
        draft=payload.draft,
    )
    await session.commit()

    try:
        instance = await app.state.workflow_client.start_instance(
            payload.process_definition_id,
            created_by=payload.created_by,
            business_key=case_id,
            initial_data=payload.initial_data,
            x_dms_principal=x_dms_principal,
        )
    except ProcessDefinitionUnknownError as exc:
        await repository.delete_unstarted_case(session, case_id)
        await session.commit()
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    case.process_instance_id = instance["id"]
    await session.commit()
    await publish_event(
        "case.created",
        subject=case_id,
        payload={"name": payload.name, "created_by": payload.created_by},
        actor=payload.created_by,
    )
    # Real per-case RBAC resource (Post-Roadmap Phase 35 Session 2, ADR
    # 0144). `parent_id="root"` so the case's node inherits root's
    # "everyone" role assignment by default (ADR 0070's "everyone gets
    # case.read/case.write" stays the default), while still letting an
    # admin narrow a SPECIFIC case's own `RoleAssignment`s afterward via the
    # already-generic `POST /role-assignments`. The SYNCHRONOUS `POST
    # /resources` call (not just the event below) is deliberate: this
    # response is about to return `case_id` to the caller, who may
    # immediately act on it (the per-case endpoints below now check
    # `resource_id=case_id`, and an unregistered resource_id denies
    # everyone outright) - a purely event-driven registration, sufficient
    # for `folder-service` (whose own CRUD never self-checks per-resource),
    # would leave exactly that race window open here. `permission-service`
    # is already a hard synchronous dependency of every request via
    # `_require_case_permission` above, so this adds no new failure mode.
    await app.state.permission_client.create_resource_node(
        resource_id=case_id, parent_id="root", resource_type="case"
    )
    # Also published for symmetry with `folder-service`'s own structure-event
    # contract and as a self-healing mechanism (see the startup backfill
    # loop in `lifespan`) - a harmless no-op here since the row already
    # exists (`structure_consumer.py`'s handler is idempotent).
    await publish_event(
        "case.resource.created",
        subject=case_id,
        payload={"resource_id": case_id, "parent_id": "root", "resource_type": "case"},
        actor=payload.created_by,
    )
    # Fully-automated process, closed synchronously right here instead of
    # relying on `workflow.instance.completed` (Post-Roadmap Phase 44
    # Session 4 - a real, previously-open race: a process with no manual
    # task at all completes synchronously inside `start_instance` above,
    # BEFORE this `Case` row is ever committed, so workflow-service may
    # publish that event before it exists - `consumer.py`'s handler then
    # finds no matching case and silently drops it for good (ACKed, no
    # retry), leaving the case stuck `"open"` forever. `instance["status"]`
    # is already known here, synchronously, from `start_instance`'s own
    # response - no need to wait for or race against the event at all for
    # THIS specific, already-known-at-creation-time case.
    if instance["status"] == "completed":
        closed = await status_transitions.close_with_validation(
            session,
            case,
            object_type_client=app.state.object_type_client,
            snapshots={},
            publish_event=publish_event,
            actor=payload.created_by,
        )
        await session.commit()
        if closed:
            await publish_event(
                "case.closed",
                subject=case_id,
                payload={"process_instance_id": instance["id"]},
                actor=payload.created_by,
            )
    return case


@app.post("/cases/{case_id}/register", response_model=CaseOut)
async def register_case(
    case_id: str,
    payload: CaseRegisterRequest,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseOut:
    """Draft -> registered transition (post-roadmap phase 31 session 2, ADR
    0113) - assigns the Vorgangsnummer at this point instead of at creation
    time (see `draft` on `POST /cases`)."""
    await _get_case_or_404(session, case_id)
    await _require_case_permission(x_dms_principal, access_type="write", resource_id=case_id)
    vorgangsnummer = await repository.next_vorgangsnummer(session)
    try:
        case = await repository.register_case(session, case_id, vorgangsnummer=vorgangsnummer)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.AlreadyRegisteredError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "case.registered",
        subject=case_id,
        payload={"vorgangsnummer": vorgangsnummer},
        actor=payload.registered_by,
    )
    return case


@app.get("/cases", response_model=list[CaseOut])
async def list_cases(
    status: str | None = None,
    object_type_id: int | None = None,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[CaseOut]:
    await _require_case_permission(x_dms_principal, access_type="read")
    cases = await repository.list_cases(session, status=status, object_type_id=object_type_id)
    return await _filter_cases_by_permission(x_dms_principal, cases)


@app.get("/cases/by-vorgangsnummer", response_model=list[CaseOut])
async def list_cases_by_vorgangsnummer(
    value: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[CaseOut]:
    """For the new `mail-connector` (2.5/3.3, P15-S3) - registered before
    `/cases/{case_id}` so that `"by-vorgangsnummer"` isn't interpreted as
    `{case_id}` (same route-ordering rule as `/cases/due-for-archival`
    below)."""
    await _require_case_permission(x_dms_principal, access_type="read")
    cases = await repository.list_cases_by_vorgangsnummer(session, value)
    return await _filter_cases_by_permission(x_dms_principal, cases)


@app.get("/cases/due-for-archival", response_model=list[CaseOut])
async def list_cases_due_for_archival(
    session: AsyncSession = Depends(get_session),
) -> list[CaseOut]:
    """Internal call from `archival-service` (5.6, since P7-S3b) -
    registered before `/cases/{case_id}` so that `"due-for-archival"` isn't
    interpreted as `{case_id}` (same route-ordering rule as
    `/documents/deleted` in document-service). Deliberately UNGATED
    (post-roadmap Phase 19 Session 5, ADR 0070) - a pure machine-to-machine
    callback with no human principal, `archival-service` currently sends no
    identity header for this at all. Same, already-preexisting gap as
    `document-service`'s analogous `PUT /documents/{id}/archived` (also
    ungated) - a general service-to-service authentication scheme is a
    larger, project-wide decision outside the scope of this session."""
    return await repository.list_due_for_archival(session)


@app.get("/cases/{case_id}", response_model=CaseOut)
async def get_case(
    case_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseOut:
    case = await _get_case_or_404(session, case_id)
    await _require_case_permission(x_dms_principal, access_type="read", resource_id=case_id)
    return case


@app.post(
    "/cases/{case_id}/documents",
    response_model=CaseDocumentReferenceOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_case_document(
    case_id: str,
    payload: CaseDocumentAdd,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseDocumentReferenceOut:
    await _get_case_or_404(session, case_id)
    await _require_case_permission(x_dms_principal, access_type="write", resource_id=case_id)
    document = await app.state.document_client.get(payload.document_id)
    if document is None:
        raise HTTPException(
            status_code=400, detail=f"document_id {payload.document_id!r} unbekannt"
        )
    try:
        reference = await repository.add_document_reference(
            session, case_id, document_id=payload.document_id, added_by=payload.added_by
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.CaseClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    case = await repository.get_case(session, case_id)
    await session.commit()
    await publish_event(
        "case.document.added",
        subject=case_id,
        payload={"document_id": payload.document_id, "added_by": payload.added_by},
        actor=payload.added_by,
    )
    return await _resolve_reference(session, case, reference)


@app.delete("/cases/{case_id}/documents/{document_id}", response_model=CaseDocumentReferenceOut)
async def remove_case_document(
    case_id: str,
    document_id: str,
    payload: CaseDocumentRemove,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseDocumentReferenceOut:
    await _get_case_or_404(session, case_id)
    await _require_case_permission(x_dms_principal, access_type="write", resource_id=case_id)
    try:
        reference = await repository.remove_document_reference(
            session, case_id, document_id, removed_by=payload.removed_by
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.CaseClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    case = await repository.get_case(session, case_id)
    await session.commit()
    await publish_event(
        "case.document.removed",
        subject=case_id,
        payload={"document_id": document_id, "removed_by": payload.removed_by},
        actor=payload.removed_by,
    )
    return await _resolve_reference(session, case, reference)


@app.get("/cases/{case_id}/documents", response_model=list[CaseDocumentReferenceOut])
async def list_case_documents(
    case_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[CaseDocumentReferenceOut]:
    case = await _get_case_or_404(session, case_id)
    await _require_case_permission(x_dms_principal, access_type="read", resource_id=case_id)
    references = await repository.list_document_references(session, case_id)
    return [await _resolve_reference(session, case, reference) for reference in references]


@app.post("/cases/{case_id}/archive-request", response_model=CaseOut)
async def request_case_archive(
    case_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseOut:
    """Manual records disposal trigger (5.6, since P7-S3b) - `409` if the
    circulation folder is not yet closed. A human action (unlike `PUT
    .../archived` below), therefore gated since P19-S5."""
    await _get_case_or_404(session, case_id)
    await _require_case_permission(x_dms_principal, access_type="write", resource_id=case_id)
    try:
        case = await repository.request_archive(session, case_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.CaseNotClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return case


@app.get("/cases/{case_id}/archive-status", response_model=CaseArchiveStatusOut)
async def get_case_archive_status(
    case_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseArchiveStatusOut:
    case = await _get_case_or_404(session, case_id)
    await _require_case_permission(x_dms_principal, access_type="read", resource_id=case_id)
    return CaseArchiveStatusOut(
        case_id=case.id, archive_after=case.archive_after, archived_at=case.archived_at
    )


@app.put("/cases/{case_id}/archived", response_model=CaseOut)
async def mark_case_archived(case_id: str, session: AsyncSession = Depends(get_session)) -> CaseOut:
    """Internal callback from `archival-service` once the XDOMEA package is
    verified (5.6, since P7-S3b). Deliberately UNGATED (post-roadmap Phase
    19 Session 5, ADR 0070) - same rationale as `GET /cases/due-for-archival`
    above: a pure machine-to-machine callback, `archival-service` sends no
    `X-DMS-Principal` for this."""
    try:
        case = await repository.mark_archived(session, case_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "case.archived", subject=case_id, payload={}, actor="system:archival-service"
    )
    return case


@app.get("/case-archival-config", response_model=CaseArchivalConfigOut)
async def get_case_archival_config(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseArchivalConfigOut:
    await _require_case_permission(x_dms_principal, access_type="read")
    return await repository.get_archival_config(session)


@app.put("/case-archival-config", response_model=CaseArchivalConfigOut)
async def update_case_archival_config(
    payload: CaseArchivalConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseArchivalConfigOut:
    await _require_case_permission(x_dms_principal, access_type="write")
    config = await repository.update_archival_config(
        session,
        default_archive_after_days_closed=payload.default_archive_after_days_closed,
        archive_encryption_enabled=payload.archive_encryption_enabled,
    )
    await session.commit()
    return config


@app.get("/case-number-config", response_model=CaseNumberConfigOut)
async def get_case_number_config(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseNumberConfigOut:
    await _require_case_permission(x_dms_principal, access_type="read")
    return await repository.get_case_number_config(session)


@app.put("/case-number-config", response_model=CaseNumberConfigOut)
async def update_case_number_config(
    payload: CaseNumberConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseNumberConfigOut:
    await _require_case_permission(x_dms_principal, access_type="write")
    try:
        config = await repository.update_case_number_format(session, format=payload.format)
    except repository.InvalidFieldError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return config


# --- Attribute-level pseudonymization vault (5.2, Phase 58 Session 1) ------
#
# Mirrors document-service's ADR 0156/folder-service's Phase 58 Session 1
# mechanism exactly - manual-only here, no automatic retention-expiry
# trigger: case-service has no `retention_until`/`full_deletion` mechanism
# at all (unlike Document/Folder), so there is no poll-loop phase to hook
# an auto-trigger into. A future session could add one if case-service
# ever gains its own retention concept.


def _get_pseudonymization_key() -> bytes:
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
    """Reuses the same global domain-admin capability document-service's/
    folder-service's identically-named endpoints already established
    (`admin.attribute_pseudonymization`) - no `permission-service` change
    needed."""
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
    "/cases/{case_id}/attributes/{attribute_name}/pseudonymize",
    response_model=PseudonymizedAttributeOut,
    status_code=status.HTTP_201_CREATED,
)
async def pseudonymize_case_attribute(
    case_id: str,
    attribute_name: str,
    payload: PseudonymizeAttributeRequest,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> PseudonymizedAttributeOut:
    """Mirrors `document_service.main.pseudonymize_document_attribute`/
    `folder_service.main.pseudonymize_folder_attribute` exactly."""
    await _require_pseudonymization_permission(x_dms_principal)
    try:
        case = await repository.get_case(session, case_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if case.object_type_id is None:
        raise HTTPException(
            status_code=400,
            detail="Umlaufmappe hat keinen Objekttyp - kein Attributschema bekannt",
        )
    object_type = await app.state.object_type_client.get(case.object_type_id)
    if object_type is None:
        raise HTTPException(status_code=400, detail="Objekttyp der Umlaufmappe nicht gefunden")
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

    value = case.attributes.get(attribute_name)
    if value is None or value == "":
        raise HTTPException(status_code=400, detail=f"Attribut {attribute_name!r} hat keinen Wert")

    key = _get_pseudonymization_key()
    encrypted_value = crypto.encrypt(json.dumps(value).encode("utf-8"), key)

    try:
        vault_entry = await repository.pseudonymize_attribute(
            session,
            case_id,
            attribute_name,
            encrypted_value=encrypted_value,
            pseudonymized_by=payload.pseudonymized_by,
            reason=payload.reason,
        )
    except repository.AlreadyPseudonymizedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "case.attribute.pseudonymized",
        subject=case_id,
        payload={"attribute_name": attribute_name},
        actor=payload.pseudonymized_by,
    )
    return vault_entry


@app.post(
    "/cases/{case_id}/attributes/{attribute_name}/reveal",
    response_model=RevealedAttributeOut,
)
async def reveal_case_attribute(
    case_id: str,
    attribute_name: str,
    payload: RevealAttributeRequest,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> RevealedAttributeOut:
    """Mirrors `document_service.main.reveal_document_attribute` exactly."""
    await _require_reveal_permission(x_dms_principal)
    try:
        vault_entry = await repository.get_pseudonymized_attribute(session, case_id, attribute_name)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    key = _get_pseudonymization_key()
    try:
        original_value = json.loads(crypto.decrypt(vault_entry.encrypted_value, key))
    except crypto.DecryptionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await repository.mark_revealed(session, vault_entry.id, revealed_by=payload.revealed_by)
    await session.commit()
    await publish_event(
        "case.attribute.revealed",
        subject=case_id,
        payload={"attribute_name": attribute_name},
        actor=payload.revealed_by,
    )
    return RevealedAttributeOut(
        case_id=case_id,
        attribute_name=attribute_name,
        value=original_value,
        reason=vault_entry.reason,
        pseudonymized_by=vault_entry.pseudonymized_by,
        pseudonymized_at=vault_entry.pseudonymized_at,
    )


@app.get(
    "/cases/{case_id}/attributes/pseudonymized",
    response_model=list[PseudonymizedAttributeOut],
)
async def list_pseudonymized_case_attributes(
    case_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[PseudonymizedAttributeOut]:
    """Mirrors `document_service.main.list_pseudonymized_document_
    attributes` - gated like any other regular case read (`case.read`),
    NOT the admin-only reveal capability, same existence-before-permission
    order every other per-case endpoint already uses (ADR 0144)."""
    try:
        await repository.get_case(session, case_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_case_permission(x_dms_principal, access_type="read", resource_id=case_id)
    return await repository.list_pseudonymized_attributes(session, case_id)
