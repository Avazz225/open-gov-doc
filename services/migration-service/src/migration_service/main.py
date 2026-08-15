import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
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
from migration_service import consumer, repository, transfer_steps
from migration_service.approval_client import ApprovalClient
from migration_service.dms_client import LocalDmsClient, RoleAssignmentInfo
from migration_service.license_client import LicenseStatusClient
from migration_service.models import Base, Transfer
from migration_service.peer_client import PeerClient
from migration_service.schemas import (
    PairedInstallationCreate,
    PairedInstallationCreateOut,
    PairedInstallationOut,
    TransferCreate,
    TransferOut,
    TransferStartResult,
)
from migration_service.settings import Settings
from migration_service.workflow_client import WorkflowServiceClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_RESOURCES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "resources")


def _read_resource(name: str) -> str:
    with open(os.path.join(_RESOURCES_DIR, name), encoding="utf-8") as f:
        return f.read().replace(
            "__SELF_BASE_URL__", settings.self_address or "http://localhost:8000"
        )


async def _license_gate(action: str) -> None:
    license_status = await app.state.license_client.get_status()
    if license_status == "unlicensed":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Lizenz erforderlich - Komponente 'migration-service' nicht lizenziert.",
        )
    if license_status == "demo" and action == "write":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Demo-Modus aktiv - nur Lesezugriff verfügbar.",
        )


def license_gate(action: str):
    async def _check() -> None:
        await _license_gate(action)

    return _check


_CONFIG_ADMIN_PRINCIPAL_ID = "migration-service"

# Since Post-Roadmap Phase 19 Session 6 (ADR 0071, permission-service
# self-gating) additionally "domain-admin-users" (`admin.user_management`) -
# `LocalDmsClient` has since also called `POST`/`PUT /roles` and `POST`/
# `DELETE /scope-locks`, both of which require this capability.
# "domain-admin-config" remains needed for `workflow-service`'s
# `POST /process-definitions`.
_REQUIRED_ROLE_NAMES = ("domain-admin-config", "domain-admin-users")


async def _ensure_config_admin_permission() -> None:
    """Bootstrap instead of manual admin preparation: `workflow-service`'s
    `POST /process-definitions` requires the domain-admin capability
    `admin.object_config` (P6-S6 retrofit), `permission-service`'s own
    `POST`/`PUT /roles`+`POST`/`DELETE /scope-locks` have required
    `admin.user_management` since P19-S6 - both roles are among the roles
    shipped by default with `permission-service` (4.6), migration-service
    assigns them to itself idempotently (same bootstrap pattern as
    `workflow-service`'s own test fixture `_grant_config_admin_permission`,
    but here as a real application startup step instead of only in
    tests)."""
    async with httpx.AsyncClient(
        base_url=settings.permission_service_base_url, timeout=10.0
    ) as client:
        roles = (await client.get("/roles")).json()
        existing = (
            await client.get(
                "/role-assignments", params={"principal_id": _CONFIG_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        existing_role_ids = {a["role_id"] for a in existing}
        for role_name in _REQUIRED_ROLE_NAMES:
            role = next((r for r in roles if r["name"] == role_name), None)
            if role is None:
                logger.warning("migration_service_domain_admin_role_missing: %r", role_name)
                continue
            if role["id"] in existing_role_ids:
                continue
            response = await client.post(
                "/role-assignments",
                json={
                    "principal_type": "service",
                    "principal_id": _CONFIG_ADMIN_PRINCIPAL_ID,
                    "role_id": role["id"],
                    "resource_id": "root",
                },
            )
            response.raise_for_status()


async def _start_transfer(
    session: AsyncSession,
    *,
    source_folder_id: str,
    target_installation_id: str,
    created_by: str,
    dry_run: bool,
    retention_days: int | None,
) -> Transfer:
    """Creates the `transfer` row and starts the associated BPMN instance
    (7.2: "itself runs as an auditable, resumable workflow"). Shared path
    for direct execution (`POST /transfers` without an active four-eyes
    check) and deferred execution after approval (`consumer.py`)."""
    await repository.get_paired_installation(session, target_installation_id)
    resolved_retention_days = (
        retention_days if retention_days is not None else settings.default_retention_days
    )
    transfer = await repository.create_transfer(
        session,
        source_folder_id=source_folder_id,
        target_installation_id=target_installation_id,
        created_by=created_by,
        dry_run=dry_run,
        retention_days=resolved_retention_days,
    )
    # Instance ID is determined HERE (by the caller), not generated by
    # workflow-service (see `WorkflowServiceClient.start_instance` docstring)
    # - AND already saved+committed on the transfer row before the actual
    # start. Without this, if the very first automatic step failed (e.g.
    # "lock" unreachable), there would be no way to later find the instance
    # that was nonetheless created in workflow-service via
    # `POST /instances/{id}/retry` - encountered in practice before this
    # flow was changed.
    instance_id = str(uuid.uuid4())
    await repository.set_workflow_instance(session, transfer.id, instance_id)
    # Commit BEFORE the workflow start (not only upon returning): the first
    # `connector_call` service task fires synchronously back onto this
    # service's own `/transfers/{id}/steps/*` endpoints (self-loopback in
    # the test case, but even with real installation pairing the BPMN
    # execution starts immediately) - a new request/transaction would
    # otherwise not yet see the row (only `flush()`, no `commit()`), same
    # pattern as ADR 0028 (federation-hub-service, P6-S9) already documents
    # for exactly this problem.
    await session.commit()
    definition_id = app.state.dry_run_definition_id if dry_run else app.state.transfer_definition_id
    initial_data: dict = {"transfer_id": transfer.id}
    if not dry_run:
        initial_data["retention_duration"] = f"P{resolved_retention_days}D"
    await app.state.workflow_client.start_instance(
        definition_id, created_by=created_by, initial_data=initial_data, instance_id=instance_id
    )
    return transfer


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS migration"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.local_dms = LocalDmsClient(settings)
    app.state.license_client = LicenseStatusClient(
        settings.registry_service_base_url or "",
        settings.service_name,
        settings.license_status_cache_ttl_seconds,
    )
    app.state.approval_client = ApprovalClient(settings.permission_service_base_url)
    app.state.workflow_client = WorkflowServiceClient(settings.workflow_service_base_url)

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    await _ensure_config_admin_permission()
    app.state.transfer_definition_id = await app.state.workflow_client.ensure_process_definition(
        name="migration_transfer", bpmn_xml=_read_resource("migration_transfer.bpmn")
    )
    app.state.dry_run_definition_id = await app.state.workflow_client.ensure_process_definition(
        name="migration_dry_run", bpmn_xml=_read_resource("migration_dry_run.bpmn")
    )

    event_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await event_bus.connect()
    app.state.event_bus = event_bus

    async def _start_transfer_from_consumer(session: AsyncSession, **kwargs) -> None:
        try:
            await _start_transfer(session, **kwargs)
        except repository.NotFoundError:
            logger.warning("migration_transfer_start_after_approval_unknown_target: %r", kwargs)

    consumer_task = asyncio.create_task(
        consumer.start_consuming(
            event_bus,
            "permission.approval.approved",
            app.state.session_factory,
            _start_transfer_from_consumer,
            settings.approval_action_type,
        )
    )

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        capabilities=["transfer"],
        sensors=http_sensor_declarations(),
    )

    logger.info("migration_service_startup_completed")
    yield

    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    consumer_task.cancel()
    if registration:
        await registration.stop()
    await event_bus.close()
    app.state.local_dms.close()
    await app.state.license_client.close()
    await app.state.approval_client.close()
    await app.state.workflow_client.close()
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


def _make_peer_client(installation) -> PeerClient:
    return PeerClient(
        base_url=installation.base_url,
        api_key=installation.api_key,
        timeout=settings.peer_call_timeout_seconds,
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


# --- Installation pairing (7.2, direct pair instead of hub) ----------------


@app.post(
    "/paired-installations",
    response_model=PairedInstallationCreateOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(license_gate("write"))],
)
async def create_paired_installation(
    payload: PairedInstallationCreate, session: AsyncSession = Depends(get_session)
) -> PairedInstallationCreateOut:
    installation, api_key = await repository.create_paired_installation(
        session,
        display_name=payload.display_name,
        base_url=payload.base_url,
        api_key=payload.api_key,
    )
    await session.commit()
    return PairedInstallationCreateOut(
        id=installation.id,
        display_name=installation.display_name,
        base_url=installation.base_url,
        created_at=installation.created_at,
        api_key=api_key,
    )


@app.get(
    "/paired-installations",
    response_model=list[PairedInstallationOut],
    dependencies=[Depends(license_gate("read"))],
)
async def list_paired_installations(
    session: AsyncSession = Depends(get_session),
) -> list[PairedInstallationOut]:
    return await repository.list_paired_installations(session)


@app.delete(
    "/paired-installations/{installation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(license_gate("write"))],
)
async def delete_paired_installation(
    installation_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.delete_paired_installation(session, installation_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


# --- Transfers (source role) --------------------------------------------------


@app.post(
    "/transfers", response_model=TransferStartResult, dependencies=[Depends(license_gate("write"))]
)
async def create_transfer(
    payload: TransferCreate, session: AsyncSession = Depends(get_session)
) -> TransferStartResult:
    if await app.state.approval_client.requires_approval(settings.approval_action_type):
        request = await app.state.approval_client.create_request(
            action_type=settings.approval_action_type,
            initiated_by=payload.created_by,
            payload={
                "source_folder_id": payload.source_folder_id,
                "target_installation_id": payload.target_installation_id,
                "created_by": payload.created_by,
                "dry_run": payload.dry_run,
                "retention_days": payload.retention_days,
            },
        )
        return TransferStartResult(status="pending_approval", approval_request_id=request["id"])

    try:
        transfer = await _start_transfer(
            session,
            source_folder_id=payload.source_folder_id,
            target_installation_id=payload.target_installation_id,
            created_by=payload.created_by,
            dry_run=payload.dry_run,
            retention_days=payload.retention_days,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    return TransferStartResult(status="started", transfer=transfer)


@app.get(
    "/transfers/{transfer_id}",
    response_model=TransferOut,
    dependencies=[Depends(license_gate("read"))],
)
async def get_transfer(
    transfer_id: str, session: AsyncSession = Depends(get_session)
) -> TransferOut:
    try:
        return await repository.get_transfer(session, transfer_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get(
    "/transfers", response_model=list[TransferOut], dependencies=[Depends(license_gate("read"))]
)
async def list_transfers(
    status: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[TransferOut]:
    return await repository.list_transfers(session, status=status)


# --- Step endpoints (target of the `connector_call` service tasks) ---------


@app.post("/transfers/{transfer_id}/steps/lock")
async def step_lock(transfer_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    transfer = await _get_transfer_or_404(session, transfer_id)
    try:
        result = await transfer_steps.lock(session, transfer, app.state.local_dms)
    except Exception as exc:
        await _fail_transfer(session, transfer, exc)
        raise
    await session.commit()
    return result


@app.post("/transfers/{transfer_id}/steps/copy")
async def step_copy(transfer_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    transfer = await _get_transfer_or_404(session, transfer_id)
    installation = await repository.get_paired_installation(
        session, transfer.target_installation_id
    )
    peer = _make_peer_client(installation)
    try:
        result = await transfer_steps.copy(
            session, transfer, app.state.local_dms, peer, created_by=transfer.created_by
        )
    except Exception as exc:
        await _fail_transfer(session, transfer, exc)
        raise
    finally:
        peer.close()
    await session.commit()
    return result


@app.post("/transfers/{transfer_id}/steps/verify")
async def step_verify(
    transfer_id: str, data: dict, session: AsyncSession = Depends(get_session)
) -> dict:
    transfer = await _get_transfer_or_404(session, transfer_id)
    installation = await repository.get_paired_installation(
        session, transfer.target_installation_id
    )
    peer = _make_peer_client(installation)
    try:
        result = await transfer_steps.verify(session, transfer, peer, data)
    except transfer_steps.StepError:
        await session.commit()
        raise HTTPException(status_code=422, detail=transfer.error_message) from None
    finally:
        peer.close()
    await session.commit()
    return result


@app.post("/transfers/{transfer_id}/steps/release")
async def step_release(transfer_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    transfer = await _get_transfer_or_404(session, transfer_id)
    installation = await repository.get_paired_installation(
        session, transfer.target_installation_id
    )
    peer = _make_peer_client(installation)
    try:
        result = await transfer_steps.release(session, transfer, app.state.local_dms, peer)
    except Exception as exc:
        await _fail_transfer(session, transfer, exc)
        raise
    finally:
        peer.close()
    await session.commit()
    return result


@app.post("/transfers/{transfer_id}/steps/delete-source")
async def step_delete_source(
    transfer_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    transfer = await _get_transfer_or_404(session, transfer_id)
    try:
        result = await transfer_steps.delete_source(
            session, transfer, folder_service_base_url=settings.folder_service_base_url
        )
    except Exception as exc:
        await _fail_transfer(session, transfer, exc)
        raise
    await session.commit()
    return result


@app.post("/transfers/{transfer_id}/steps/dry-run-check")
async def step_dry_run_check(
    transfer_id: str, session: AsyncSession = Depends(get_session)
) -> dict:
    transfer = await _get_transfer_or_404(session, transfer_id)
    installation = await repository.get_paired_installation(
        session, transfer.target_installation_id
    )
    peer = _make_peer_client(installation)
    try:
        result = await transfer_steps.dry_run_check(transfer, peer)
    finally:
        peer.close()
    transfer.status = "dry_run_completed"
    await session.flush()
    await session.commit()
    return result


async def _get_transfer_or_404(session: AsyncSession, transfer_id: str) -> Transfer:
    try:
        return await repository.get_transfer(session, transfer_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


async def _fail_transfer(session: AsyncSession, transfer: Transfer, exc: Exception) -> None:
    """Persists the failure BEFORE the exception is re-raised - without this,
    `GET /transfers/{id}` would keep showing the previous status even though
    the step actually failed (resumability, 7.2). `rollback()` + reload
    first: if the original exception was itself a failed flush (e.g. a data
    type error), the session is already in `PendingRollbackError` state - a
    direct access to `transfer.error_message` (lazily-loaded attribute)
    would then trigger a second, obscuring exception instead of reporting
    the real error (encountered in practice)."""
    await session.rollback()
    transfer = await repository.get_transfer(session, transfer.id)
    if transfer.error_message is None:
        transfer.error_message = str(exc)
    transfer.status = "failed"
    await session.flush()
    await session.commit()


# --- Inbound API (target role) -------------------------------------------------


async def _authenticate(authorization: str | None, session: AsyncSession):
    presented = None
    if authorization and authorization.lower().startswith("bearer "):
        presented = authorization[len("bearer ") :]
    try:
        return await repository.authenticate_peer(session, presented)
    except repository.UnauthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@app.post("/inbound/transfers")
async def inbound_announce_transfer(
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    installation = await _authenticate(authorization, session)
    target_folder = await asyncio.to_thread(
        app.state.local_dms.tree.create_folder,
        parent_id=payload["target_parent_folder_id"],
        name=payload["name"],
        created_by=f"migration-service (von {installation.display_name})",
    )
    await repository.create_inbound_transfer(
        session,
        transfer_id=payload["transfer_id"],
        source_installation_id=installation.id,
        target_folder_id=target_folder.id,
    )
    await session.commit()
    return {"target_folder_id": target_folder.id}


@app.post("/inbound/transfers/{transfer_id}/folders")
async def inbound_push_folder(
    transfer_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _authenticate(authorization, session)
    await _get_inbound_transfer_or_404(session, transfer_id)
    folder = await asyncio.to_thread(
        app.state.local_dms.tree.create_folder,
        parent_id=payload["parent_target_folder_id"],
        name=payload["name"],
        created_by=payload["created_by"],
    )
    return {"id": folder.id, "name": folder.name, "parent_id": folder.parent_id}


@app.post("/inbound/transfers/{transfer_id}/documents")
async def inbound_push_document(
    transfer_id: str,
    target_folder_id: str = Form(...),
    created_by: str = Form(...),
    existing_target_document_id: str | None = Form(None),
    expected_base_version_number: int | None = Form(None),
    file: UploadFile = File(...),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _authenticate(authorization, session)
    await _get_inbound_transfer_or_404(session, transfer_id)
    content = await file.read()
    document = await asyncio.to_thread(
        app.state.local_dms.tree.write_document,
        folder_id=target_folder_id,
        filename=file.filename or "dokument",
        content=content,
        content_type=file.content_type,
        created_by=created_by,
        existing_document_id=existing_target_document_id,
        expected_base_version_number=expected_base_version_number,
    )
    return {
        "id": document.id,
        "title": document.title,
        "current_version_number": document.current_version_number,
        "checksum_sha256": document.checksum_sha256,
    }


@app.post("/inbound/transfers/{transfer_id}/permissions")
async def inbound_push_permission(
    transfer_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _authenticate(authorization, session)
    await _get_inbound_transfer_or_404(session, transfer_id)
    await asyncio.to_thread(
        app.state.local_dms.apply_role_assignment,
        payload["target_resource_id"],
        RoleAssignmentInfo(
            principal_type=payload["principal_type"],
            principal_id=payload["principal_id"],
            role_name=payload["role_name"],
            role_description=payload["role_description"],
            role_permissions=payload["role_permissions"],
        ),
    )
    return {"status": "ok"}


@app.post("/inbound/transfers/{transfer_id}/verify")
async def inbound_verify(
    transfer_id: str,
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _authenticate(authorization, session)
    await _get_inbound_transfer_or_404(session, transfer_id)
    checksums = {}
    for document_id in payload.get("document_ids", []):
        try:
            document = await asyncio.to_thread(app.state.local_dms.tree.get_document, document_id)
        except Exception:  # noqa: BLE001 - report missing checksum instead of crashing
            checksums[document_id] = None
            continue
        checksums[document_id] = document.checksum_sha256
    return {"checksums": checksums}


@app.post("/inbound/transfers/{transfer_id}/release")
async def inbound_release(
    transfer_id: str,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _authenticate(authorization, session)
    await _get_inbound_transfer_or_404(session, transfer_id)
    return {"status": "released"}


@app.post("/inbound/transfers/dry-run-check")
async def inbound_dry_run_check(
    payload: dict,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _authenticate(authorization, session)
    try:
        await asyncio.to_thread(
            app.state.local_dms.tree.get_folder, payload["target_parent_folder_id"]
        )
        return {"ok": True, "problems": []}
    except Exception as exc:  # noqa: BLE001 - result, not a crash
        return {"ok": False, "problems": [str(exc)]}


async def _get_inbound_transfer_or_404(session: AsyncSession, transfer_id: str):
    try:
        return await repository.get_inbound_transfer(session, transfer_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
