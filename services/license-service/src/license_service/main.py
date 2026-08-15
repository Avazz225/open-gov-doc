import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from license_service import license_state, usage
from license_service.clients import (
    AuthServiceClient,
    DocumentClient,
    PermissionServiceClient,
    StorageClient,
)
from license_service.license_verifier import InvalidLicenseError, decode
from license_service.models import Base
from license_service.poll_loop import UsageClients, poll_loop
from license_service.schemas import DimensionUsageOut, LicenseStatusOut, LicenseUploadRequest
from license_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def publish_event(
    event_type: str, subject: str | None, payload: dict, actor: str | None = None
) -> None:
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
        actor=actor,
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


async def _is_active_superuser(x_dms_principal: str) -> bool:
    active, superuser_principal_id = await app.state.auth_client.get_active_superuser()
    return active and bool(x_dms_principal) and superuser_principal_id == x_dms_principal


def _is_fleet_agent(authorization: str | None) -> bool:
    """3a/P13-S2: the independently operated `fleet-management-service` has
    no Keycloak principal in this installation - it authenticates instead
    via the installation-wide, optional `settings.fleet_agent_api_key`
    (`DMS_FLEET_AGENT_API_KEY`, `dms_common.BaseServiceSettings`). If the
    key is not configured (default), this path is completely disabled -
    pure opt-in (3a: "optional, separate building block")."""
    return bool(settings.fleet_agent_api_key) and authorization == (
        f"Bearer {settings.fleet_agent_api_key}"
    )


async def _require_license_permission(
    x_dms_principal: str, authorization: str | None = None
) -> None:
    """Activates for the first time the long-since-preseeded domain-admin
    role `domain-admin-license` (`admin.license`) - the activated
    superuser (4.6) and the fleet-agent key are the two exceptions, same
    gating pattern as query-service (superuser bypass)."""
    if _is_fleet_agent(authorization):
        return
    is_superuser = await _is_active_superuser(x_dms_principal)
    if is_superuser:
        return
    allowed = bool(x_dms_principal) and await app.state.permission_client.has_permission(
        x_dms_principal, settings.license_permission
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Lizenzverwaltung'",
        )


def _usage_clients() -> UsageClients:
    return UsageClients(
        storage_client=app.state.storage_client,
        document_client=app.state.document_client,
        auth_client=app.state.auth_client,
    )


async def _status_out(claims: dict | None) -> LicenseStatusOut:
    if claims is None:
        return LicenseStatusOut(
            installed=False, valid=False, invalid_reason="Keine Lizenz installiert"
        )
    snapshot = await usage.compute_usage(
        claims,
        storage_client=app.state.storage_client,
        document_client=app.state.document_client,
        auth_client=app.state.auth_client,
        local_installation_id=settings.installation_id,
    )
    return LicenseStatusOut(
        installed=True,
        valid=snapshot.valid,
        invalid_reason=snapshot.invalid_reason,
        issued_at=snapshot.issued_at,
        expires_at=snapshot.expires_at,
        days_remaining=snapshot.days_remaining,
        user_model=snapshot.user_model,
        users=DimensionUsageOut(
            limit=snapshot.users.limit,
            current=snapshot.users.current,
            exceeded=snapshot.users.exceeded,
        ),
        storage_gb=DimensionUsageOut(
            limit=snapshot.storage_gb.limit,
            current=snapshot.storage_gb.current,
            exceeded=snapshot.storage_gb.exceeded,
        ),
        documents=DimensionUsageOut(
            limit=snapshot.documents.limit,
            current=snapshot.documents.current,
            exceeded=snapshot.documents.exceeded,
        ),
        licensed_components=snapshot.licensed_components,
        limits_exceeded=snapshot.limits_exceeded,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()

    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS license"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    app.state.storage_client = StorageClient(settings.storage_service_base_url)
    app.state.document_client = DocumentClient(settings.document_service_base_url)
    app.state.auth_client = AuthServiceClient(settings.auth_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)

    event_bus = NatsEventBusClient(settings.nats_url, stream="license")
    await event_bus.connect()
    app.state.event_bus = event_bus

    poll_task = asyncio.create_task(
        poll_loop(app.state.session_factory, _usage_clients(), settings, publish_event)
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
    logger.info("Startup completed in %s ms.", millis)

    yield

    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await poll_task
    await event_bus.close()
    await app.state.storage_client.close()
    await app.state.document_client.close()
    await app.state.auth_client.close()
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


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


@app.post("/license", response_model=LicenseStatusOut, status_code=status.HTTP_201_CREATED)
async def upload_license(
    payload: LicenseUploadRequest,
    x_dms_principal: str = Header(default=""),
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> LicenseStatusOut:
    """9.2: install a signed license file. Only an invalid signature
    results in `400` - a license with a valid signature but already
    expired is still stored and shown as invalid via the status (see
    PROGRESS.md architecture decision "Upload rejection")."""
    await _require_license_permission(x_dms_principal, authorization)

    try:
        claims = decode(
            payload.license_token,
            public_key_pem=settings.license_public_key_pem,
            previous_public_key_pem=settings.license_previous_public_key_pem,
        )
    except InvalidLicenseError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    issued_at = datetime.fromtimestamp(claims["iat"], tz=UTC) if claims.get("iat") else None
    expires_at = datetime.fromtimestamp(claims["exp"], tz=UTC) if claims.get("exp") else None
    await license_state.install(
        session,
        raw_token=payload.license_token,
        installed_by=x_dms_principal,
        issued_at=issued_at,
        expires_at=expires_at,
    )
    await session.commit()

    await publish_event("license.installed", None, {}, actor=x_dms_principal)
    return await _status_out(claims)


@app.get("/license/status", response_model=LicenseStatusOut)
async def license_status(session: AsyncSession = Depends(get_session)) -> LicenseStatusOut:
    """Ungated (concept 9.3: "Registry Service delivers the license status
    to every registering service") - is also queried by other services
    without a principal header, see P9-S2."""
    license_row = await license_state.get_installed(session)
    if license_row is None:
        return await _status_out(None)
    try:
        claims = decode(
            license_row.raw_token,
            public_key_pem=settings.license_public_key_pem,
            previous_public_key_pem=settings.license_previous_public_key_pem,
        )
    except InvalidLicenseError:
        return LicenseStatusOut(
            installed=True, valid=False, invalid_reason="Lizenzsignatur ungueltig"
        )
    return await _status_out(claims)
