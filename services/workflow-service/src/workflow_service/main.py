import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime
from typing import Literal

import httpx
from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workflow_service import consumer, federation_crypto, repository, spiff_adapter
from workflow_service.approval_client import ApprovalClient
from workflow_service.federation_client import FederationHubClient
from workflow_service.license_client import LicenseStatusClient
from workflow_service.models import Base, FederationConfig, FederationIdentity
from workflow_service.permission_client import PermissionServiceClient
from workflow_service.schemas import (
    BusinessCalendarCreate,
    BusinessCalendarOut,
    BusinessCalendarUpdate,
    DmnDefinitionDetailOut,
    DmnDefinitionOut,
    FederationConfigOut,
    FederationConfigUpdate,
    ProcessDefinitionDetailOut,
    ProcessDefinitionImportResult,
    ProcessDefinitionOut,
    ProcessInstanceCreate,
    ProcessInstanceOut,
    ReadyTaskOut,
    ReadyTaskWithInstanceOut,
    TaskCompleteRequest,
)
from workflow_service.settings import Settings
from workflow_service.signature_client import SignatureServiceClient

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_SIGNATURE_LEVEL_RANK = {"ses": 0, "aes": 1, "qes": 2}
_FEDERATION_IDENTITY_ID = 1
_FEDERATION_CONFIG_ID = 1
_FEDERATED_TASK_TYPES = ("federated", "federated_return")


# Own, swappable synchronous client (instead of the free `httpx.post()`
# function) - tests replace it via `monkeypatch` with an `httpx.Client` using
# `httpx.MockTransport`, the same stubbing principle as `federation-hub-service`'s
# tests (there `httpx.AsyncClient`/`ASGITransport`, here synchronous, see below).
_connector_http_client = httpx.Client()


def _handle_connector_task(extensions: dict[str, str], data: dict) -> dict:
    """Registered with `spiff_adapter.ConnectorServiceTask` (7.1 "triggering a
    connector call", P12-S2) - deliberately the only place in this service
    that calls `httpx` synchronously: SpiffWorkflow's `do_engine_steps()` is
    entirely synchronous (no `async`/`await` anywhere in the engine), so a
    call here already blocks the surrounding `async def` request handler
    anyway - consistent with every other SpiffWorkflow interaction in this
    service (see `spiff_adapter.py` module docstring), no new async/sync
    bridge needed. This service itself has no knowledge of `serviceUrl` -
    completely generic, no knowledge of the calling service (migration-
    service is the first, but not the only conceivable user). `serviceUrl`
    supports `{placeholder}` substitution from the current process data
    (`str.format(**data)`) - this lets, for example, a per-instance
    `transfer_id` flow into the URL, without the BPMN file itself needing
    to be generated individually per instance."""
    service_url = extensions.get("serviceUrl")
    if not service_url:
        raise RuntimeError("connector_call Service Task ohne serviceUrl-Extension")
    try:
        service_url = service_url.format(**data)
    except (KeyError, IndexError) as exc:
        raise RuntimeError(
            f"serviceUrl {service_url!r} referenziert eine unbekannte Prozessvariable: {exc}"
        ) from exc
    response = _connector_http_client.post(
        service_url, json=data, timeout=settings.connector_call_timeout_seconds
    )
    response.raise_for_status()
    body = response.json()
    return body if isinstance(body, dict) else {}


spiff_adapter.register_connector_task_handler(_handle_connector_task)


async def _apply_approved_process_definition_import(
    name: str, bpmn_xml: str, process_id: str | None
) -> None:
    """Applies a BPMN import approved via the four-eyes principle
    (Post-Roadmap Phase 21 Session 4, ADR 0087) - the same
    `repository.create_process_definition` that the immediate, ungated
    path in `create_process_definition` (endpoint) also uses, here just
    with its own session instead of the request session (no HTTP request
    context in the consumer, see `consumer.py`). BPMN validation was
    deliberately deferred at the original request (like
    `config_service._apply_config_document` also only performs its schema
    validation here) - an invalid BPMN therefore only fails now, not
    already when the approval request is created."""
    async with app.state.session_factory() as session:
        try:
            await repository.create_process_definition(
                session, name=name, bpmn_xml=bpmn_xml, process_id=process_id
            )
        except repository.InvalidBpmnError:
            logger.exception(
                "Genehmigter BPMN-Import für Prozessfamilie %r ist ungültiges BPMN "
                "(Validierung war bei der Anfrage bewusst zurückgestellt, siehe ADR 0087)",
                name,
            )
            return
        await session.commit()


async def _sla_poll_loop(
    session_factory: async_sessionmaker[AsyncSession], permission_client: PermissionServiceClient
) -> None:
    """SLA time monitoring (P6-S2, ADR 0020): polls instead of reacting in a
    push-based way, since neither SpiffWorkflow nor this project brings its
    own background scheduler. An error in one tick does not abort the loop,
    so a single broken blob doesn't stop SLA monitoring for all other
    running instances. Since P6-S6, additionally: skips the tick while
    maintenance mode is active (4.8) - "scheduled/periodic jobs are
    paused"."""
    while True:
        try:
            if await permission_client.is_maintenance_active():
                await asyncio.sleep(settings.sla_poll_interval_seconds)
                continue
            async with session_factory() as session:
                results = await repository.advance_timers(session)
                await session.commit()
            for result in results:
                for fired in result.fired:
                    await publish_event(
                        "workflow.task.escalated",
                        subject=result.instance.id,
                        payload={
                            "process_definition_id": result.instance.process_definition_id,
                            "business_key": result.instance.business_key,
                            "task_name": fired.name,
                            "lane": fired.lane,
                            "escalation_email": fired.data.get("escalation_email"),
                        },
                        actor="system:sla-poll",
                    )
                if result.newly_completed:
                    await publish_event(
                        "workflow.instance.completed",
                        subject=result.instance.id,
                        payload={"business_key": result.instance.business_key},
                        actor="system:sla-poll",
                    )
        except Exception:
            logger.exception(
                "SLA-Poll-Tick fehlgeschlagen - wird beim nächsten Tick erneut versucht."
            )
        await asyncio.sleep(settings.sla_poll_interval_seconds)


async def _get_or_seed_federation_config(session: AsyncSession) -> FederationConfig:
    """7.4/P13-S3: the version compatibility declaration has lived in this
    DB row since P13-S3 instead of directly in `Settings` - seeded from the
    (still valid) `Settings` defaults on the very first access,
    backward-compatible with all installations that have never executed a
    `PUT /federation/config`."""
    config = await session.get(FederationConfig, _FEDERATION_CONFIG_ID)
    if config is None:
        config = FederationConfig(
            id=_FEDERATION_CONFIG_ID,
            version=settings.installation_version,
            min_compatible_peer_version=settings.installation_min_compatible_peer_version,
            updated_at=datetime.now(UTC),
        )
        session.add(config)
        await session.flush()
    return config


async def _ensure_federation_identity(
    session_factory: async_sessionmaker[AsyncSession],
) -> FederationHubClient | None:
    """One-time self-registration at the Federation Hub (7.4, P6-S9) -
    opt-in, stays `None` without a configured
    `settings.federation_hub_base_url` (see
    `docs/services/workflow-service.md` "Federation"). Analogous to
    `maybe_start_registration` from `dms-registry-client`, but deliberately
    without reusing that lib: the Hub is not an internal service-discovery
    entry of this installation but an external, cross-installation service
    with its own registration/auth protocol (API key instead of heartbeat,
    RSA key pair instead of health endpoint, see
    `federation_client.py`/`federation_crypto.py`)."""
    if not settings.federation_hub_base_url:
        return None
    client = FederationHubClient(settings.federation_hub_base_url)
    callback_base_url = f"{settings.installation_gateway_base_url}/api/workflow-service"
    async with session_factory() as session:
        config = await _get_or_seed_federation_config(session)
        identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
        if identity is None:
            private_pem, public_pem = federation_crypto.generate_keypair()
            hub_public_key_pem = await client.get_hub_public_key()
            installation_id = str(uuid.uuid4())
            await client.register(
                installation_id=installation_id,
                private_key_pem=private_pem,
                display_name=settings.installation_display_name,
                callback_base_url=callback_base_url,
                public_key_pem=public_pem.decode("utf-8"),
                version=config.version,
                min_compatible_peer_version=config.min_compatible_peer_version,
            )
            identity = FederationIdentity(
                id=_FEDERATION_IDENTITY_ID,
                installation_id=installation_id,
                private_key_pem=private_pem,
                public_key_pem=public_pem,
                hub_public_key_pem=hub_public_key_pem.encode("utf-8"),
                created_at=datetime.now(UTC),
            )
            session.add(identity)
            await session.commit()
        else:
            # Re-registration on every start (upsert like with the internal
            # registry) - keeps e.g. `callback_base_url`/`version` current
            # if Settings/`FederationConfig` have changed between restarts.
            # A failure (Hub currently unreachable) does not block our own
            # start - Federation is a bonus feature, not a hard dependency
            # of this installation.
            try:
                await client.register(
                    installation_id=identity.installation_id,
                    private_key_pem=identity.private_key_pem,
                    display_name=settings.installation_display_name,
                    callback_base_url=callback_base_url,
                    public_key_pem=identity.public_key_pem.decode("utf-8"),
                    version=config.version,
                    min_compatible_peer_version=config.min_compatible_peer_version,
                )
            except httpx.HTTPError:
                logger.warning("federation_hub_reregistration_failed")
    return client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS workflow"))
        await conn.run_sync(Base.metadata.create_all)
        # Process versioning (P6-S8): `name` was previously globally unique,
        # is now the process family key - uniqueness has since applied to
        # (name, version). `create_all` only creates missing TABLES/
        # constraints for new deployments but doesn't alter existing ones -
        # hence ad hoc for already-existing databases (no Alembic in this
        # early phase, see CONTRIBUTING.md). Postgres has no
        # `ADD CONSTRAINT IF NOT EXISTS`, but it does have
        # `DROP CONSTRAINT IF EXISTS` and `CREATE UNIQUE INDEX IF NOT EXISTS`
        # - a unique index is equivalent to the former unique constraint
        # enforcement.
        await conn.execute(
            text(
                "ALTER TABLE workflow.process_definition "
                "ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE workflow.process_definition "
                "DROP CONSTRAINT IF EXISTS process_definition_name_key"
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_process_definition_name_version "
                "ON workflow.process_definition (name, version)"
            )
        )
        # Federation Hub (P6-S9): `federation_task.handover_id` was initially
        # unique on its own - but in the self-loopback smoke test (an
        # installation hands over to itself), both the outbound and the
        # inbound row carry the same `handover_id` in the same database, see
        # `models.FederationTask`. Uniqueness has since applied to
        # `(handover_id, direction)` - the same idempotent migration pattern
        # as for the process definition versioning above.
        await conn.execute(
            text("DROP INDEX IF EXISTS workflow.ix_workflow_federation_task_handover_id")
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_workflow_federation_task_handover_id "
                "ON workflow.federation_task (handover_id)"
            )
        )
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_federation_task_handover_direction "
                "ON workflow.federation_task (handover_id, direction)"
            )
        )
        # Signature-based Hub authentication instead of API key (P13-S4,
        # ADR 0039) - `api_key` is removed in the same step instead of being
        # deliberately deferred: the old model is fully replaced here, no
        # rolling-update scenario between old/new to consider, see the same
        # reasoning in `federation_hub_service.main`.
        await conn.execute(
            text("ALTER TABLE workflow.federation_identity DROP COLUMN IF EXISTS api_key")
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    # Business calendar cache (P14-S5): `business_days()` reads it
    # synchronously from within `spiff_adapter.py` (no DB access possible
    # from SpiffWorkflow's synchronous `has_fired()` evaluation) - loaded
    # once at startup, after which `repository.py` keeps it current after
    # every write access. Must come before the SLA poll loop below, so an
    # already-running timer doesn't hit an empty cache.
    async with app.state.session_factory() as session:
        await repository.refresh_business_calendar_cache(session)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.signature_client = SignatureServiceClient(settings.signature_service_base_url)
    app.state.federation_client = await _ensure_federation_identity(app.state.session_factory)
    app.state.license_client = LicenseStatusClient(
        settings.registry_service_base_url or "",
        settings.service_name,
        settings.license_status_cache_ttl_seconds,
    )

    # Pure producer (no consumer, see docs/services/workflow-service.md
    # "Events") - own stream, a producer must create it itself (ADR 0001).
    event_bus = NatsEventBusClient(settings.nats_url, stream="workflow")
    await event_bus.connect()
    app.state.event_bus = event_bus

    # BPMN import review gate (4.3, Post-Roadmap Phase 21 Session 4, ADR 0087):
    app.state.approval_client = ApprovalClient(settings.permission_service_base_url)
    # Pure consumer, no own stream (`ensure_stream=False`) - unlike
    # `event_bus` above (workflow-service's own `"workflow"` stream), this
    # second, separate client only reacts to permission-service's
    # already-existing `permission.approval.approved` event, same pattern
    # as `config_service.main.lifespan`.
    approval_consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await approval_consumer_bus.connect()
    app.state.approval_consumer_bus = approval_consumer_bus
    await consumer.start_consuming(
        approval_consumer_bus, settings.approval_subjects, _apply_approved_process_definition_import
    )

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    sla_poll_task = asyncio.create_task(
        _sla_poll_loop(app.state.session_factory, app.state.permission_client)
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    sla_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await sla_poll_task
    if registration:
        await registration.stop()
    await event_bus.close()
    await approval_consumer_bus.close()
    await app.state.approval_client.close()
    await app.state.permission_client.close()
    await app.state.signature_client.close()
    await app.state.license_client.close()
    if app.state.federation_client is not None:
        await app.state.federation_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(
    event_type: str,
    subject: str,
    payload: dict,
    actor: str | None = None,
    on_behalf_of: str | None = None,
) -> None:
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
        actor=actor,
        on_behalf_of=on_behalf_of,
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


async def _dispatch_outbound_federated_task(
    session: AsyncSession, instance_id: str, task: spiff_adapter.TaskInfo
) -> None:
    """Automatic handover (7.4): a `taskType=federated` task is never
    completed by a human (see `_reject_manual_federated_completion`), but
    handed over to the Federation Hub immediately once ready."""
    identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
    target_installation_id = task.extensions.get("targetInstallationId")
    target_process_type = task.extensions.get("targetProcessType")
    if identity is None or not target_installation_id or not target_process_type:
        logger.warning(
            "federated_task_missing_configuration",
            extra={"instance_id": instance_id, "task_id": task.id},
        )
        return

    installations = await app.state.federation_client.list_installations()
    target = next((i for i in installations if i["id"] == target_installation_id), None)
    if target is None:
        logger.warning(
            "federated_task_unknown_target",
            extra={"instance_id": instance_id, "target_installation_id": target_installation_id},
        )
        return

    encrypted_payload = federation_crypto.encrypt_for(
        target["public_key_pem"].encode("utf-8"), task.data
    )

    # `handover_id` is deliberately generated here (not by the Hub), and our
    # own FederationTask row is committed already BEFORE the Hub call: the
    # Hub can deliver synchronously back into this very installation (e.g. a
    # handover to our own installation_id, see ADR 0028 "self-loopback") -
    # without this commit the row would not yet be visible on a nested
    # callback (`/federation/inbound-result`) due to Postgres transaction
    # isolation, since the outer transaction only commits after returning
    # from `create_handover()`.
    handover_id = str(uuid.uuid4())
    await repository.create_federation_task(
        session,
        process_instance_id=instance_id,
        task_id=task.id,
        handover_id=handover_id,
        direction="outbound",
        origin_installation_id=None,
        status="pending",
    )
    await session.commit()

    try:
        handover = await app.state.federation_client.create_handover(
            installation_id=identity.installation_id,
            private_key_pem=identity.private_key_pem,
            handover_id=handover_id,
            to_installation_id=target_installation_id,
            process_type=target_process_type,
            encrypted_payload=encrypted_payload,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "federated_handover_creation_failed: %s",
            exc,
            extra={"instance_id": instance_id, "task_id": task.id},
        )
        return

    federation_task = await repository.get_federation_task_by_handover(session, handover_id)
    if federation_task is not None:
        await repository.update_federation_task_status(session, federation_task, handover["status"])
        await session.commit()


async def _dispatch_federated_return_task(
    session: AsyncSession, instance_id: str, task: spiff_adapter.TaskInfo
) -> None:
    """Counterpart on the receiving side (7.4): automatically sends the
    result of a `taskType=federated_return` task back to the original
    installation via the Federation Hub as soon as the task is ready."""
    identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
    inbound = await repository.get_inbound_federation_task_for_instance(session, instance_id)
    if identity is None or inbound is None or inbound.origin_installation_id is None:
        logger.warning(
            "federated_return_task_missing_origin",
            extra={"instance_id": instance_id, "task_id": task.id},
        )
        return

    installations = await app.state.federation_client.list_installations()
    origin = next((i for i in installations if i["id"] == inbound.origin_installation_id), None)
    if origin is None:
        logger.warning(
            "federated_return_task_unknown_origin",
            extra={
                "instance_id": instance_id,
                "origin_installation_id": inbound.origin_installation_id,
            },
        )
        return

    encrypted_result = federation_crypto.encrypt_for(
        origin["public_key_pem"].encode("utf-8"), task.data
    )
    try:
        result = await app.state.federation_client.send_result(
            installation_id=identity.installation_id,
            private_key_pem=identity.private_key_pem,
            handover_id=inbound.handover_id,
            outcome="completed",
            encrypted_result=encrypted_result,
        )
    except httpx.HTTPError as exc:
        logger.warning(
            "federated_return_send_failed: %s",
            exc,
            extra={"instance_id": instance_id, "task_id": task.id},
        )
        return

    # The Hub itself responds with 200 even if the forwarding to the origin
    # installation behind it fails (see
    # `federation_hub_service.main.submit_handover_result`) - `result["status"]`
    # reflects the actual delivery success, not just our own successful
    # handover to the Hub.
    status = "returned" if result.get("status") == "completed" else "return_delivery_failed"
    await repository.mark_inbound_federation_task_returned(
        session, inbound, task_id=task.id, status=status
    )


async def _dispatch_pending_federation_tasks(session: AsyncSession, instance_id: str) -> None:
    """Called after every operation that can create newly ready tasks
    (instance start, task completion, SLA poll tick, federation inbound) -
    detects newly ready `federated`/`federated_return` tasks and triggers
    the matching action. Already-dispatched tasks are skipped via
    `FederationTask` rows (no duplicate delivery)."""
    if app.state.federation_client is None:
        return
    try:
        tasks = await repository.get_ready_tasks(session, instance_id)
    except repository.NotFoundError:
        return
    for task in tasks:
        task_type = task.extensions.get("taskType")
        if task_type not in _FEDERATED_TASK_TYPES:
            continue
        if await repository.get_federation_task_by_task(session, instance_id, task.id) is not None:
            continue
        if task_type == "federated":
            await _dispatch_outbound_federated_task(session, instance_id, task)
        else:
            await _dispatch_federated_return_task(session, instance_id, task)


async def _require_object_config(x_dms_principal: str) -> None:
    """Retrofit P6-S6: process definitions (including script task upload,
    per `docs/services/workflow-service.md` "a real security concern") are
    from now on an administrative action, not regular functional use -
    requires the domain admin capability `admin.object_config` (the same
    "object type/workflow configuration" role from P6-S5, now actually
    enforced for the first time, including a real technical account
    `config-admin`). Instance start/task completion, on the other hand,
    originally (P6-S6 clarification decision) did not need a domain admin
    role - since Post-Roadmap Phase 19 Session 9 (ADR 0074) they instead
    check `workflow.write`, see `_require_workflow_permission` below (RBAC
    instead of the admin domain, no contradiction with the original
    decision "no domain admin role needed")."""
    allowed = bool(x_dms_principal) and await app.state.permission_client.has_permission(
        x_dms_principal, "admin.object_config"
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Objekttyp-/Workflow-Konfiguration'",
        )


async def _require_workflow_permission(x_dms_principal: str, *, access_type: str) -> None:
    """RBAC (Post-Roadmap Phase 19 Session 9, ADR 0074) - instance start/
    task completion were previously deliberately open to every
    authenticated principal (P6-S6 clarification decision, see
    `_require_object_config` above) - this session turns that into a real,
    admin-editable RBAC check (`workflow.write`) instead of a hardcoded
    open path, exactly the pattern already established in P19-S5/S7/S8.
    The "everyone" group (ADR 0067) grants `workflow.write` by default -
    preserves the previous de-facto-open behavior ("normal functional use
    shouldn't need a domain admin role"), but makes it admin-editable."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=PermissionServiceClient.ROOT_RESOURCE_ID,
        permission="workflow.write",
        access_type=access_type,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail="Fehlende Berechtigung 'workflow.write'")


async def _reject_during_maintenance(x_dms_maintenance_active: str) -> None:
    """Emergency shutdown (4.8, P6-S6): "all running workflow instances ...
    paused" is implemented as "no new instances/no progress while the
    lock is active" (see ADR 0024 for the reasoning behind this
    limitation)."""
    if x_dms_maintenance_active.lower() == "true":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Systemweite Notfallsperre aktiv - Wartungsmodus",
        )


def _license_gate(action: Literal["read", "write"]):
    """Demo mode/lock behavior (Concept 9.3, P9-S2): workflow-service is
    the only licensable "application component" (9.1) that actually
    exists today. `"unlicensed"` locks completely (including reads),
    `"demo"` allows only read access (concept example verbatim). As a
    `Depends()` instead of manually in the function body like with
    maintenance mode, since an async cross-service call is needed here,
    not a simple header read. Federation endpoints are deliberately left
    out (a self-contained topic, Phase 13)."""

    async def _check() -> None:
        license_status = await app.state.license_client.get_status()
        if license_status == "unlicensed":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Lizenz erforderlich - Komponente 'workflow-service' nicht lizenziert.",
            )
        if license_status == "demo" and action == "write":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Demo-Modus aktiv - nur Lesezugriff verfügbar.",
            )

    return _check


@app.post(
    "/process-definitions",
    response_model=ProcessDefinitionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_license_gate("write"))],
)
async def create_process_definition(
    bpmn_xml: UploadFile = File(...),
    name: str = Form(...),
    process_id: str | None = Form(None),
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ProcessDefinitionOut | JSONResponse:
    """Since Post-Roadmap Phase 21 Session 4 (ADR 0087), optionally gated
    via the generic four-eyes mechanism (`workflow.process_definition.
    import`) - an uploaded BPMN document can contain script tasks/connector
    calls (see `docs/services/workflow-service.md` "a real security
    concern"), a second admin can review it before activation. By default
    (no configuration), behavior stays UNCHANGED: immediate creation,
    `201` + `ProcessDefinitionOut` as before - deliberately NOT always
    wrapped in a status envelope like `config_service`'s
    `POST /config/import` (ADR 0060), since this endpoint (unlike there)
    is firmly relied upon, as test/setup infrastructure, by very many
    existing callers (process designer, dozens of tests) for the unchanged
    success form - only the new, previously non-existent
    `pending_approval` case gets its own form (`202` +
    `ProcessDefinitionImportResult`), recognizable by the HTTP status."""
    await _require_object_config(x_dms_principal)
    xml_bytes = await bpmn_xml.read()
    try:
        xml_text = xml_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="bpmn_xml ist kein gültiges UTF-8") from exc

    if await app.state.approval_client.requires_approval("workflow.process_definition.import"):
        request = await app.state.approval_client.create_request(
            action_type="workflow.process_definition.import",
            initiated_by=x_dms_principal,
            payload={"name": name, "bpmn_xml": xml_text, "process_id": process_id},
        )
        pending = ProcessDefinitionImportResult(
            status="pending_approval", approval_request_id=request["id"]
        )
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED, content=pending.model_dump(mode="json")
        )

    try:
        definition = await repository.create_process_definition(
            session, name=name, bpmn_xml=xml_text, process_id=process_id
        )
    except repository.InvalidBpmnError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return definition


@app.get(
    "/process-definitions",
    response_model=list[ProcessDefinitionOut],
    dependencies=[Depends(_license_gate("read"))],
)
async def list_process_definitions(
    name: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[ProcessDefinitionOut]:
    """Without `name`: newest version per process family (P6-S8). With
    `name`: complete version history of that family, newest first."""
    return await repository.list_process_definitions(session, name=name)


@app.get(
    "/process-definitions/{process_definition_id}",
    response_model=ProcessDefinitionDetailOut,
    dependencies=[Depends(_license_gate("read"))],
)
async def get_process_definition(
    process_definition_id: int, session: AsyncSession = Depends(get_session)
) -> ProcessDefinitionDetailOut:
    try:
        return await repository.get_process_definition(session, process_definition_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(
    "/process-definitions/{process_definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_license_gate("write"))],
)
async def delete_process_definition(
    process_definition_id: int,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_object_config(x_dms_principal)
    try:
        await repository.delete_process_definition(session, process_definition_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.ProcessDefinitionInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()


@app.post(
    "/process-definitions/{process_definition_id}/restore",
    response_model=ProcessDefinitionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_license_gate("write"))],
)
async def restore_process_definition(
    process_definition_id: int,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ProcessDefinitionOut:
    """Rollback (P25-S2): reads an arbitrary historical version of a
    process family (`process_definition_id` can be any version of this
    family, not just the respective newest) and creates from it a BRAND
    NEW, now-current version with exactly that version's
    `bpmn_xml`/`bpmn_process_id` - append-only like any other version
    creation (ADR 0027), no in-place edit/overwrite of the target version
    or any intermediate version. Calls the same (race-safe since P25-S1,
    ADR 0096) `repository.create_process_definition` as the regular upload
    path (`POST /process-definitions`) for this - thereby automatically
    inherits its advisory-lock protection against concurrent version
    assignment, without duplicating the version-number logic here.
    `process_id` is deliberately passed explicitly as the `bpmn_process_id`
    already resolved at the time of the origin version (not `None`) - a
    renewed automatic resolution is unnecessary and could even fail for a
    BPMN file with multiple top-level processes, even though the origin
    version itself was already uniquely resolved when it was created.

    Gated like `POST /process-definitions` (`admin.object_config` +
    license gate) - the same administrative action, just with content
    already known to the system instead of freshly uploaded content.
    Deliberately NOT additionally routed through the optional BPMN import
    review gate (ADR 0087, `workflow.process_definition.import`): its
    four-eyes check addresses externally uploaded content previously
    unknown to the system - the version restored here was already a
    perfectly regular version of this family before (possibly itself
    already approved once, if the gate was active at that time), not new,
    unreviewed content.

    No special case for "restoring the already-newest version" - just like
    any other call, it simply creates another, content-identical version,
    instead of rejecting with `409`/no-op - consistent with the
    append-only versioning model, no additional special rule needed."""
    await _require_object_config(x_dms_principal)
    try:
        source = await repository.get_process_definition(session, process_definition_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    try:
        definition = await repository.create_process_definition(
            session, name=source.name, bpmn_xml=source.bpmn_xml, process_id=source.bpmn_process_id
        )
    except repository.InvalidBpmnError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return definition


@app.post(
    "/dmn-definitions",
    response_model=DmnDefinitionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_license_gate("write"))],
)
async def create_dmn_definition(
    dmn_xml: UploadFile = File(...),
    name: str = Form(...),
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> DmnDefinitionOut:
    """Upload a DMN 1.3 decision table (7.1, P14-S4) - same access/
    versioning pattern as `create_process_definition`."""
    await _require_object_config(x_dms_principal)
    xml_bytes = await dmn_xml.read()
    try:
        xml_text = xml_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="dmn_xml ist kein gültiges UTF-8") from exc

    try:
        definition = await repository.create_dmn_definition(session, name=name, dmn_xml=xml_text)
    except repository.InvalidDmnError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except repository.DuplicateDecisionIdError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return definition


@app.get(
    "/dmn-definitions",
    response_model=list[DmnDefinitionOut],
    dependencies=[Depends(_license_gate("read"))],
)
async def list_dmn_definitions(
    name: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[DmnDefinitionOut]:
    """Without `name`: newest version per DMN family. With `name`: complete
    version history of that family, newest first (see
    `repository.list_dmn_definitions`)."""
    return await repository.list_dmn_definitions(session, name=name)


@app.get(
    "/dmn-definitions/{dmn_definition_id}",
    response_model=DmnDefinitionDetailOut,
    dependencies=[Depends(_license_gate("read"))],
)
async def get_dmn_definition(
    dmn_definition_id: int, session: AsyncSession = Depends(get_session)
) -> DmnDefinitionDetailOut:
    try:
        return await repository.get_dmn_definition(session, dmn_definition_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete(
    "/dmn-definitions/{dmn_definition_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_license_gate("write"))],
)
async def delete_dmn_definition(
    dmn_definition_id: int,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_object_config(x_dms_principal)
    try:
        await repository.delete_dmn_definition(session, dmn_definition_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


@app.post(
    "/business-calendars",
    response_model=BusinessCalendarOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_license_gate("write"))],
)
async def create_business_calendar(
    payload: BusinessCalendarCreate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> BusinessCalendarOut:
    """Regional business calendar for SLA deadline calculation (7.1, P14-S5)
    - gated like process/DMN definitions (`admin.object_config`), but NO
    versioning pattern: `name` remains permanently unique (`409` on
    duplicate), editing runs via `PUT`, not via a repeat `POST`."""
    await _require_object_config(x_dms_principal)
    try:
        calendar = await repository.create_business_calendar(
            session,
            name=payload.name,
            non_working_dates=payload.non_working_dates,
            is_default=payload.is_default,
        )
    except repository.DuplicateBusinessCalendarNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except repository.InvalidBusinessCalendarError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return calendar


@app.get(
    "/business-calendars",
    response_model=list[BusinessCalendarOut],
    dependencies=[Depends(_license_gate("read"))],
)
async def list_business_calendars(
    session: AsyncSession = Depends(get_session),
) -> list[BusinessCalendarOut]:
    return await repository.list_business_calendars(session)


@app.get(
    "/business-calendars/{business_calendar_id}",
    response_model=BusinessCalendarOut,
    dependencies=[Depends(_license_gate("read"))],
)
async def get_business_calendar(
    business_calendar_id: int, session: AsyncSession = Depends(get_session)
) -> BusinessCalendarOut:
    try:
        return await repository.get_business_calendar(session, business_calendar_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.put(
    "/business-calendars/{business_calendar_id}",
    response_model=BusinessCalendarOut,
    dependencies=[Depends(_license_gate("write"))],
)
async def update_business_calendar(
    business_calendar_id: int,
    payload: BusinessCalendarUpdate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> BusinessCalendarOut:
    await _require_object_config(x_dms_principal)
    try:
        calendar = await repository.update_business_calendar(
            session,
            business_calendar_id,
            name=payload.name,
            non_working_dates=payload.non_working_dates,
            is_default=payload.is_default,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.DuplicateBusinessCalendarNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except repository.InvalidBusinessCalendarError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return calendar


@app.delete(
    "/business-calendars/{business_calendar_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(_license_gate("write"))],
)
async def delete_business_calendar(
    business_calendar_id: int,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_object_config(x_dms_principal)
    try:
        await repository.delete_business_calendar(session, business_calendar_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


@app.post(
    "/process-definitions/{process_definition_id}/instances",
    response_model=ProcessInstanceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(_license_gate("write"))],
)
async def start_instance(
    process_definition_id: int,
    payload: ProcessInstanceCreate,
    x_dms_maintenance_active: str = Header(default="false"),
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ProcessInstanceOut:
    await _reject_during_maintenance(x_dms_maintenance_active)
    await _require_workflow_permission(x_dms_principal, access_type="write")
    try:
        instance = await repository.start_instance(
            session,
            process_definition_id,
            created_by=payload.created_by,
            business_key=payload.business_key,
            initial_data=payload.initial_data,
            instance_id=payload.instance_id,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.InvalidBpmnError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:
        # An automatic step (e.g. `connector_call`, P12-S2) can fail AFTER
        # `repository.start_instance()` has already flushed the instance
        # with its current (possibly `ERROR`) intermediate state (see its
        # `try`/`finally`) - without this commit here, `get_session()`'s
        # context manager would roll back the flush when closing the
        # session (`AsyncSession.close()` without a prior `commit()`), and
        # `POST /instances/{id}/retry` would find no instance at all.
        await session.commit()
        raise
    await session.commit()
    await publish_event(
        "workflow.instance.started",
        subject=instance.id,
        payload={"process_definition_id": process_definition_id, "created_by": payload.created_by},
        actor=payload.created_by,
    )
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance.id,
            payload={"business_key": instance.business_key},
            actor=payload.created_by,
        )
    await _dispatch_pending_federation_tasks(session, instance.id)
    await session.commit()
    return instance


@app.get(
    "/instances/{instance_id}",
    response_model=ProcessInstanceOut,
    dependencies=[Depends(_license_gate("read"))],
)
async def get_instance(
    instance_id: str, session: AsyncSession = Depends(get_session)
) -> ProcessInstanceOut:
    try:
        return await repository.get_instance(session, instance_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/instances",
    response_model=list[ProcessInstanceOut],
    dependencies=[Depends(_license_gate("read"))],
)
async def list_instances(
    process_definition_id: int | None = None,
    status: str | None = None,
    business_key: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ProcessInstanceOut]:
    return await repository.list_instances(
        session,
        process_definition_id=process_definition_id,
        status=status,
        business_key=business_key,
    )


@app.get(
    "/tasks",
    response_model=list[ReadyTaskWithInstanceOut],
    dependencies=[Depends(_license_gate("read"))],
)
async def list_ready_tasks(
    session: AsyncSession = Depends(get_session),
) -> list[ReadyTaskWithInstanceOut]:
    """Cross-instance task list (8, P14-S2 reviewer/approval UI) - until now
    there was only `GET /instances/{id}/tasks` (requires an already-known
    instance ID). Iterates all `running` instances (same pattern as the SLA
    poll loop, `_sla_poll_loop`) and collects their ready Manual/Signature
    tasks. `federated`/`federated_return` tasks are filtered out - those
    are completed exclusively automatically via the Federation Hub
    (`_reject_manual_federated_completion`), a human could never
    successfully complete them via this list anyway. No additional role
    gate beyond the license check, same as for instance start/task
    completion itself."""
    instances = await repository.list_instances(session, status="running")
    tasks: list[ReadyTaskWithInstanceOut] = []
    for instance in instances:
        for task in await repository.get_ready_tasks(session, instance.id):
            if task.extensions.get("taskType") in _FEDERATED_TASK_TYPES:
                continue
            tasks.append(
                ReadyTaskWithInstanceOut(
                    id=task.id,
                    name=task.name,
                    lane=task.lane,
                    data=task.data,
                    extensions=task.extensions,
                    instance_id=instance.id,
                    process_definition_id=instance.process_definition_id,
                    business_key=instance.business_key,
                )
            )
    return tasks


@app.get(
    "/instances/{instance_id}/tasks",
    response_model=list[ReadyTaskOut],
    dependencies=[Depends(_license_gate("read"))],
)
async def get_ready_tasks(
    instance_id: str, session: AsyncSession = Depends(get_session)
) -> list[ReadyTaskOut]:
    try:
        tasks = await repository.get_ready_tasks(session, instance_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [
        ReadyTaskOut(id=t.id, name=t.name, lane=t.lane, data=t.data, extensions=t.extensions)
        for t in tasks
    ]


async def _require_valid_signature_if_needed(
    session: AsyncSession, instance_id: str, task_id: str, payload: TaskCompleteRequest
) -> None:
    """Signature Task (3.10, P6-S7): a task marked as `taskType=signature`
    (see `spiff_adapter.py`'s Camunda extensions) requires a real signature
    created at the Signature Service instead of an arbitrary completion -
    `document_id` comes from the generic task process data (`data`, no own
    schema field, since workflow-service has no document concept),
    `requiredLevel` from the BPMN extensions (default `ses` if not set)."""
    tasks = await repository.get_ready_tasks(session, instance_id)
    task = next((t for t in tasks if t.id == task_id), None)
    if task is None or task.extensions.get("taskType") != "signature":
        return

    if not payload.signature_id:
        raise HTTPException(
            status_code=400, detail="Signature Task verlangt eine signature_id im Request-Body"
        )
    signature = await app.state.signature_client.get_signature(payload.signature_id)
    if signature is None:
        raise HTTPException(
            status_code=400, detail=f"signature_id {payload.signature_id!r} unbekannt"
        )

    expected_document_id = task.data.get("document_id")
    if not expected_document_id:
        raise HTTPException(
            status_code=400,
            detail="Signature Task ohne document_id in den Prozessdaten - nicht signierbar",
        )
    if signature["document_id"] != expected_document_id:
        raise HTTPException(
            status_code=400,
            detail=(
                f"signature_id {payload.signature_id!r} gehört zu Dokument "
                f"{signature['document_id']!r}, erwartet wurde {expected_document_id!r}"
            ),
        )

    required_level = task.extensions.get("requiredLevel", "ses")
    if _SIGNATURE_LEVEL_RANK[signature["level"]] < _SIGNATURE_LEVEL_RANK[required_level]:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Signature Task verlangt mindestens Niveau {required_level!r}, "
                f"signature_id {payload.signature_id!r} hat Niveau {signature['level']!r}"
            ),
        )


async def _reject_manual_federated_completion(
    session: AsyncSession, instance_id: str, task_id: str
) -> None:
    """A `federated`/`federated_return` task (7.4, P6-S9) is completed
    exclusively automatically via the Federation Hub (see
    `_dispatch_pending_federation_tasks`) - a direct `.../complete` call
    (accidental or by a human) is rejected, otherwise a handover result
    could never be delivered again because the task is already finished
    locally."""
    tasks = await repository.get_ready_tasks(session, instance_id)
    task = next((t for t in tasks if t.id == task_id), None)
    if task is not None and task.extensions.get("taskType") in _FEDERATED_TASK_TYPES:
        raise HTTPException(
            status_code=409,
            detail="Dieser Task wird automatisch über den Federation Hub abgeschlossen",
        )


async def _require_delegation_if_on_behalf_of(
    session: AsyncSession, instance_id: str, payload: TaskCompleteRequest, x_dms_principal: str
) -> None:
    """Deputizing during absence (4.4a, P14-S11) - ``completed_by`` remains
    an unvalidated free-text field (an existing, deliberate gap, see
    docs/services/workflow-service.md), but a completion "on behalf of"
    requires a REAL delegation checked against the Permission Service: the
    calling identity for this comes from the gateway-injected
    ``X-DMS-Principal`` header, NOT from the freely choosable
    `completed_by` - otherwise any caller could claim an arbitrary
    delegation simply by writing a different name into the free-text
    field."""
    if not payload.on_behalf_of_principal_id:
        return
    if not x_dms_principal:
        raise HTTPException(
            status_code=401,
            detail=(
                "Fehlender X-DMS-Principal-Header für einen Abschluss "
                "im Auftrag einer anderen Person"
            ),
        )
    instance = await repository.get_instance(session, instance_id)
    allowed = await app.state.permission_client.check_delegation(
        deputy_principal_id=x_dms_principal,
        delegator_principal_id=payload.on_behalf_of_principal_id,
        process_definition_id=instance.process_definition_id,
    )
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Keine aktive Delegation von {payload.on_behalf_of_principal_id!r} an "
                f"{x_dms_principal!r} für diesen Prozess"
            ),
        )


@app.post(
    "/instances/{instance_id}/tasks/{task_id}/complete",
    response_model=ProcessInstanceOut,
    dependencies=[Depends(_license_gate("write"))],
)
async def complete_task(
    instance_id: str,
    task_id: str,
    payload: TaskCompleteRequest,
    x_dms_maintenance_active: str = Header(default="false"),
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ProcessInstanceOut:
    await _reject_during_maintenance(x_dms_maintenance_active)
    await _require_workflow_permission(x_dms_principal, access_type="write")
    try:
        await _require_valid_signature_if_needed(session, instance_id, task_id, payload)
        await _reject_manual_federated_completion(session, instance_id, task_id)
        await _require_delegation_if_on_behalf_of(session, instance_id, payload, x_dms_principal)
        instance = await repository.complete_task(
            session, instance_id, task_id, completed_by=payload.completed_by, data=payload.data
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.TaskNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        # See `start_instance` - a subsequent automatic step can fail, the
        # already-flushed intermediate state must still be committed
        # (P12-S2 resumability).
        await session.commit()
        raise
    await session.commit()
    await publish_event(
        "workflow.task.completed",
        subject=instance_id,
        payload={"task_id": task_id, "completed_by": payload.completed_by},
        actor=payload.completed_by,
        on_behalf_of=payload.on_behalf_of_principal_id,
    )
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance_id,
            payload={"business_key": instance.business_key},
            actor=payload.completed_by,
            on_behalf_of=payload.on_behalf_of_principal_id,
        )
    await _dispatch_pending_federation_tasks(session, instance_id)
    await session.commit()
    return instance


@app.post(
    "/instances/{instance_id}/retry",
    response_model=ProcessInstanceOut,
    dependencies=[Depends(_license_gate("write"))],
)
async def retry_instance(
    instance_id: str,
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> ProcessInstanceOut:
    """Resumability for a failed automatic step (7.1/7.2, P12-S2) - a
    generic primitive, not a migration-specific endpoint: a `connector_call`
    service task whose `serviceUrl` was unreachable on the first attempt
    leaves the instance `running` with the affected task in `ERROR` (see
    `spiff_adapter.retry_errored_tasks`) - this endpoint retries the step
    without restarting the entire process. No additional role gate beyond
    the normal license check - `POST .../tasks/.../complete` is already
    open to every authenticated principal."""
    await _reject_during_maintenance(x_dms_maintenance_active)
    try:
        instance = await repository.retry_instance(session, instance_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.InstanceNotRunningError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception:
        # See `start_instance` - a repeated failing attempt must still be
        # committed, otherwise the instance would stay stuck at the
        # original failure point for a third `retry` attempt, instead of
        # at the actually last one.
        await session.commit()
        raise
    await session.commit()
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance_id,
            payload={"business_key": instance.business_key},
            actor="system:retry",
        )
        await session.commit()
    return instance


@app.get("/federation/installations")
async def list_federation_installations() -> list[dict]:
    """Proxy to the Hub's address book (7.4) - ungated like other `GET`s,
    see `docs/services/process-designer.md`. An empty list without a
    configured Hub, so the Process Designer doesn't even offer federated
    process steps as an option (Concept 7.1)."""
    if app.state.federation_client is None:
        return []
    return await app.state.federation_client.list_installations()


@app.get("/federation/config", response_model=FederationConfigOut)
async def get_federation_config(session: AsyncSession = Depends(get_session)) -> FederationConfig:
    """7.4/P13-S3: currently declared version compatibility range - ungated
    like the other federation read endpoints, pure metadata (no document
    content, no secret)."""
    config = await _get_or_seed_federation_config(session)
    await session.commit()
    return config


@app.put("/federation/config", response_model=FederationConfigOut)
async def update_federation_config(
    payload: FederationConfigUpdate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> FederationConfig:
    """7.4/P13-S3: makes the version compatibility range changeable via the
    regular configuration import (7.3, `config-service`) without needing a
    container restart - gated behind the same `admin.object_config`
    capability as the BPMN upload (P6-S6 retrofit), since `config-service`
    already assigns itself this role. Immediately triggers a
    re-registration at the Hub with the new values if federation is
    active (a failure does not block the response - federation remains a
    bonus feature, not a hard dependency)."""
    await _require_object_config(x_dms_principal)
    config = await _get_or_seed_federation_config(session)
    config.version = payload.version
    config.min_compatible_peer_version = payload.min_compatible_peer_version
    config.updated_at = datetime.now(UTC)
    await session.commit()

    if app.state.federation_client is not None:
        identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
        if identity is not None:
            callback_base_url = f"{settings.installation_gateway_base_url}/api/workflow-service"
            try:
                await app.state.federation_client.register(
                    installation_id=identity.installation_id,
                    private_key_pem=identity.private_key_pem,
                    display_name=settings.installation_display_name,
                    callback_base_url=callback_base_url,
                    public_key_pem=identity.public_key_pem.decode("utf-8"),
                    version=config.version,
                    min_compatible_peer_version=config.min_compatible_peer_version,
                )
            except httpx.HTTPError:
                logger.warning("federation_hub_reregistration_after_config_update_failed")
    return config


@app.post("/federation/rotate-key")
async def rotate_federation_key(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Key rotation (7.4, P13-S4, ADR 0039) - gated behind the same
    `admin.object_config` capability as `PUT /federation/config`. Generates
    a fresh RSA key pair locally, signs the rotation request with the
    still-current (old) private key (proof of continuity towards the Hub,
    see `federation_hub_service.repository.rotate_installation_key`), and
    only adopts the new key pair after a successful response from the Hub
    - if the rotation fails, the old, still-valid key pair remains
    unchanged in use."""
    await _require_object_config(x_dms_principal)
    if app.state.federation_client is None:
        raise HTTPException(status_code=503, detail="Federation Hub nicht konfiguriert")
    identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
    if identity is None:
        raise HTTPException(status_code=503, detail="Noch keine Federation-Identität vorhanden")

    new_private_pem, new_public_pem = federation_crypto.generate_keypair()
    try:
        await app.state.federation_client.rotate_key(
            installation_id=identity.installation_id,
            old_private_key_pem=identity.private_key_pem,
            new_public_key_pem=new_public_pem.decode("utf-8"),
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Schlüsselrotation beim Hub fehlgeschlagen: {exc}",
        ) from exc

    identity.private_key_pem = new_private_pem
    identity.public_key_pem = new_public_pem
    await session.commit()
    return {"installation_id": identity.installation_id, "rotated_at": datetime.now(UTC)}


async def _verify_hub_signature(
    session: AsyncSession, body: bytes, signature: str
) -> FederationIdentity:
    identity = await session.get(FederationIdentity, _FEDERATION_IDENTITY_ID)
    if identity is None:
        raise HTTPException(status_code=503, detail="Federation Hub nicht konfiguriert")
    if not federation_crypto.verify_body(identity.hub_public_key_pem, body, signature):
        raise HTTPException(status_code=401, detail="Ungültige oder fehlende Hub-Signatur")
    return identity


@app.post(
    "/federation/inbound", response_model=ProcessInstanceOut, status_code=status.HTTP_201_CREATED
)
async def federation_inbound(
    request: Request,
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> ProcessInstanceOut:
    """Receives a new handover mediated by the Federation Hub (7.4) - starts
    a new local instance of the process assigned via
    `settings.federation_process_type_map`. Deliberately public (no
    `X-DMS-Principal`, see `gateway-service`'s `public_routes`) -
    authentication instead happens via the `X-Federation-Hub-Signature`.
    Respects maintenance mode (4.8) like instance start/task completion -
    a federated step is everyday processing, not an admin operation."""
    await _reject_during_maintenance(x_dms_maintenance_active)
    body = await request.body()
    identity = await _verify_hub_signature(
        session, body, request.headers.get("X-Federation-Hub-Signature", "")
    )
    payload = json.loads(body)
    process_type = payload["process_type"]
    local_name = settings.federation_process_type_map.get(process_type)
    if local_name is None:
        raise HTTPException(
            status_code=422, detail=f"Kein lokales Prozess-Mapping für {process_type!r}"
        )
    definitions = await repository.list_process_definitions(session, name=local_name)
    if not definitions:
        raise HTTPException(status_code=422, detail=f"Keine Prozessdefinition {local_name!r}")
    try:
        decrypted_payload = federation_crypto.decrypt_with(
            identity.private_key_pem, payload["encrypted_payload"]
        )
    except federation_crypto.DecryptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    instance = await repository.start_instance(
        session,
        definitions[0].id,  # newest version first, see list_process_definitions
        created_by="federation-hub",
        business_key=None,
        initial_data=decrypted_payload,
    )
    await repository.create_federation_task(
        session,
        process_instance_id=instance.id,
        task_id=None,
        handover_id=payload["handover_id"],
        direction="inbound",
        origin_installation_id=payload["from_installation_id"],
        status="received",
    )
    await session.commit()
    await publish_event(
        "workflow.instance.started",
        subject=instance.id,
        payload={"process_definition_id": definitions[0].id, "created_by": "federation-hub"},
        actor="system:federation-hub",
    )
    await publish_event(
        "workflow.federation.inbound_received",
        subject=instance.id,
        payload={
            "business_key": instance.business_key,
            "from_installation_id": payload["from_installation_id"],
            "process_type": process_type,
            "notify_email": decrypted_payload.get("notify_email"),
        },
        actor="system:federation-hub",
    )
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance.id,
            payload={"business_key": instance.business_key},
            actor="system:federation-hub",
        )
    await _dispatch_pending_federation_tasks(session, instance.id)
    await session.commit()
    return instance


@app.post("/federation/inbound-result")
async def federation_inbound_result(
    request: Request,
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> dict:
    """Receives the response to a previously self-initiated handover (7.4) -
    completes the originally waiting `taskType=federated` task
    programmatically (not via the regular `.../complete` endpoint, which
    rejects exactly this task type, see
    `_reject_manual_federated_completion`). Respects maintenance mode (4.8)
    like other functional processing."""
    await _reject_during_maintenance(x_dms_maintenance_active)
    body = await request.body()
    identity = await _verify_hub_signature(
        session, body, request.headers.get("X-Federation-Hub-Signature", "")
    )
    payload = json.loads(body)
    federation_task = await repository.get_federation_task_by_handover(
        session, payload["handover_id"]
    )
    if federation_task is None or federation_task.task_id is None:
        raise HTTPException(
            status_code=404, detail=f"handover_id {payload['handover_id']!r} unbekannt"
        )
    try:
        result_data = federation_crypto.decrypt_with(
            identity.private_key_pem, payload["encrypted_result"]
        )
    except federation_crypto.DecryptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        instance = await repository.complete_task(
            session,
            federation_task.process_instance_id,
            federation_task.task_id,
            completed_by="federation-hub",
            data=result_data,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.TaskNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await repository.update_federation_task_status(session, federation_task, "completed")
    await session.commit()
    await publish_event(
        "workflow.task.completed",
        subject=federation_task.process_instance_id,
        payload={"task_id": federation_task.task_id, "completed_by": "federation-hub"},
        actor="system:federation-hub",
    )
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance.id,
            payload={"business_key": instance.business_key},
            actor="system:federation-hub",
        )
    await _dispatch_pending_federation_tasks(session, instance.id)
    await session.commit()
    return {"status": "ok"}
