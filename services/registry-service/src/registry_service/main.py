import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    metrics_payload,
    run_gauge_sampler_loop,
)
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from registry_service import consumer, metrics, repository
from registry_service.license_client import LicenseServiceClient
from registry_service.licensing import ComponentLicenseCache
from registry_service.models import Base
from registry_service.schemas import InstanceOut, LicenseStatusForServiceOut, RegisterRequest
from registry_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def _cleanup_poll_loop(session_factory) -> None:
    """Periodic cleanup of permanently-unreachable instance rows (3.2a,
    Phase 58 Session 2) - same poll-loop idiom as `document_service.main.
    _retention_poll_loop`/`workflow_service.main._sla_poll_loop`. An error
    in one tick doesn't abort the loop. Reuses `repository.deregister`
    (not a bulk DELETE) so each cleanup still publishes the existing
    `registry.instance.deregistered` event - consistent with the manual
    `DELETE /instances/{id}` path, just with a different actor."""
    while True:
        try:
            async with session_factory() as session:
                stale = await repository.list_permanently_unreachable(
                    session, cleanup_after_seconds=settings.unreachable_cleanup_after_seconds
                )
                for instance in stale:
                    instance_id = instance.instance_id
                    service_type = instance.service_type
                    # Internal caller (Phase 59 Session 4): passes the
                    # instance's own `service_type` as its identity - the
                    # new `X-DMS-Principal` check on `repository.deregister`
                    # exists to stop an EXTERNAL caller deregistering an
                    # instance it doesn't own, not to gate this trusted,
                    # already-instance-scoped internal cleanup loop.
                    await repository.deregister(session, instance_id, service_type)
                    await session.commit()
                    await publish_event(
                        "registry.instance.deregistered",
                        subject=instance_id,
                        payload={"service_type": service_type},
                        actor="system:registry-cleanup",
                    )
        except Exception:
            logger.exception(
                "Cleanup-Poll-Tick fehlgeschlagen - wird beim nächsten Tick erneut versucht."
            )
        await asyncio.sleep(settings.cleanup_poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        # No Alembic in this early phase - schema/tables are ensured
        # idempotently at startup. Migrations will follow once the model
        # has stabilized in production.
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS registry"))
        await conn.run_sync(Base.metadata.create_all)
        # `create_all` creates missing TABLES but does not alter existing
        # ones - `status` (drain mechanism, 10.5/3.8, P10-S2) was only added
        # afterward, same additive ad-hoc migration pattern as e.g.
        # document-service. Idempotent thanks to IF NOT EXISTS.
        await conn.execute(
            text(
                "ALTER TABLE registry.service_instance "
                "ADD COLUMN IF NOT EXISTS status VARCHAR(16) DEFAULT 'active' NOT NULL"
            )
        )
        # Sensor catalog (10.1, P11-S1) - same additive pattern.
        await conn.execute(
            text(
                "ALTER TABLE registry.service_instance "
                "ADD COLUMN IF NOT EXISTS sensors JSON DEFAULT '[]'::json NOT NULL"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    event_bus = NatsEventBusClient(settings.nats_url, stream="registry")
    await event_bus.connect()
    app.state.event_bus = event_bus

    # License mediation (concept 3.2b/9.3, P9-S2): the registry's first own
    # NATS consumer - reacts to status changes of license-service (P9-S1) by
    # invalidating the TTL cache, instead of re-querying purely on a
    # time basis.
    app.state.license_cache = ComponentLicenseCache(
        LicenseServiceClient(settings.license_service_base_url),
        licensable_components=settings.licensable_components,
        cache_ttl_seconds=settings.license_status_cache_ttl_seconds,
    )
    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    await consumer.start_consuming(consumer_bus, ["license.>"], app.state.license_cache)

    # The registry has been registering with itself since P4-S3
    # (registry_service_base_url points to its own address) - the basis for
    # the gateway also being able to resolve "registry-service" as a
    # service_type (e.g. for the admin UI's registry overview, which
    # consistently only talks through the gateway, never directly). The
    # very first registration inevitably fails (the own Uvicorn server only
    # starts accepting connections after the lifespan startup completes) -
    # the self-healing fix from `dms-registry-client` (re-registration on
    # 404 on the next heartbeat) therefore kicks in here for arguably the
    # most common case: self-registration at its own startup.
    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        sensors=metrics.sensor_declarations(),
    )

    # Sensor concept (10.1): registry-service was one of the original two
    # pilots (P11-S1), now covered by the full rollout's generic http.*
    # sensors too (`sensor_registry`/`active_gauge`/`heartbeat_miss_gauge`/
    # `sensor_config_proxy` are module-level names, built by
    # `bootstrap_http_sensors` right after `app = FastAPI(...)`). A fresh
    # `SensorConfigClient` per startup, bound into the module-level proxy,
    # not a module-level client itself (`SensorConfigProxy`'s docstring:
    # its httpx client can't outlive the event loop it was first used on).
    app.state.sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await app.state.sensor_config_client.start()
    sensor_config_proxy.bind(app.state.sensor_config_client)
    app.state.sensor_registry = sensor_registry
    samplers = metrics.build_samplers(
        active_gauge,
        heartbeat_miss_gauge,
        app.state.session_factory,
        heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds,
    )
    sensor_sampler_task = asyncio.create_task(
        run_gauge_sampler_loop(samplers, interval_seconds=settings.sensor_sample_interval_seconds)
    )

    cleanup_poll_task = asyncio.create_task(_cleanup_poll_loop(app.state.session_factory))

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    cleanup_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await cleanup_poll_task
    sensor_sampler_task.cancel()
    with suppress(asyncio.CancelledError):
        await sensor_sampler_task
    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    await consumer_bus.close()
    await event_bus.close()
    await app.state.license_cache.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

# Sensor concept (10.1): must run at module level, right after `app` is
# constructed - see `bootstrap_http_sensors`'s docstring for why this can't
# move into `lifespan` (FastAPI forbids adding middleware once the app has
# started). `sensor_registry` is shared with registry-service's own
# pre-existing pilot sensors below (one registry per service).
sensor_config_proxy, sensor_registry, _http_requests_sensor, _http_duration_sensor = (
    bootstrap_http_sensors(app, settings.service_name)
)
active_gauge, heartbeat_miss_gauge = metrics.build_sensor_registry(sensor_registry)


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


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/installation")
def get_installation() -> dict:
    """Installation identity (3a, P13-S1): a service, a tool (CLI, admin
    UI), or the future fleet/license management layer (P13-S2) can query
    here which installation is currently responding - unauthenticated and
    without DB access like `/healthz`, since `installation_id`/
    `installation_display_name` are plain configuration values (see
    `dms_common.BaseServiceSettings`), not secret data."""
    return {"id": settings.installation_id, "display_name": settings.installation_display_name}


@app.get("/metrics")
def get_metrics() -> Response:
    """Prometheus exposition of the two own sensors (10.1, P11-S1) - scraped
    by `monitoring-service`, not directly by Prometheus."""
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


def _require_operator_key(authorization: str) -> None:
    """Operator gate for drain/activate (Phase 59 Session 4) - same bearer-
    secret pattern as `federation_hub_service.main`'s `hub_operator_key`
    check (ADR 0039): fully locked (`403`) without a configured
    `registry_operator_key`, a registry operator must deliberately enable
    drain/rollback. Not an `X-DMS-Principal` check like register/heartbeat/
    deregister below - the real caller is an external ops tool/human
    operator (`scripts/rolling-update.sh`), not a self-registering service,
    confirmed by tracing every real caller before choosing a gate shape."""
    if (
        not settings.registry_operator_key
        or authorization != f"Bearer {settings.registry_operator_key}"
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlender oder ungültiger Registry-Operator-Schlüssel",
        )


@app.post("/instances", response_model=InstanceOut, status_code=status.HTTP_201_CREATED)
async def register_instance(
    payload: RegisterRequest,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> InstanceOut:
    """Self-registration only (Phase 59 Session 4) - the caller's own
    `X-DMS-Principal` must equal the `service_type` it is registering as
    (`dms_registry_client.RegistryRegistration` now sends this as a fixed
    default header, one shared-library change covering every self-
    registering service at once). Previously ungated: any caller reachable
    through the gateway could register a fake instance of an arbitrary
    `service_type` at an attacker-controlled `address`, with a real chance
    of being selected for real user traffic by `gateway_service.upstream.
    InstanceResolver` - a traffic-hijack/credential-harvesting vector, not
    just a routing curiosity."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    if x_dms_principal != payload.service_type:
        raise HTTPException(
            status_code=403,
            detail="X-DMS-Principal muss dem registrierten service_type entsprechen",
        )
    result = await repository.register(session, payload)
    await session.commit()
    result.license_status = await app.state.license_cache.status_for(result.service_type)
    await publish_event(
        "registry.instance.registered",
        subject=payload.instance_id,
        payload={"service_type": payload.service_type, "version": payload.version},
        actor=f"system:{payload.service_type}",
    )
    return result


@app.post("/instances/{instance_id}/heartbeat", response_model=InstanceOut)
async def send_heartbeat(
    instance_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> InstanceOut:
    """Same self-service gate as `register_instance` above (Phase 59
    Session 4) - the caller's own `X-DMS-Principal` must equal the target
    instance's own `service_type`. Existence (`404`) is checked before
    identity, same ordering convention as the rest of this project - unlike
    `register_instance` (which has no existing row to protect), a missing
    header here is indistinguishable from a mismatched one (both fail the
    same `!=` comparison against a real `service_type`), so this collapses
    to a single `403` rather than a separate `401` pre-check that would
    have to run before the existence check to keep the same ordering."""
    try:
        result = await repository.heartbeat(session, instance_id, x_dms_principal)
    except repository.InstanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instance not registered") from exc
    except repository.PermissionDeniedError as exc:
        raise HTTPException(
            status_code=403,
            detail="X-DMS-Principal muss dem service_type der Instanz entsprechen",
        ) from exc
    await session.commit()
    result.license_status = await app.state.license_cache.status_for(result.service_type)
    return result


@app.post("/instances/{instance_id}/drain", response_model=InstanceOut)
async def drain_instance(
    instance_id: str,
    authorization: str = Header(default="", alias="Authorization"),
    session: AsyncSession = Depends(get_session),
) -> InstanceOut:
    """Drain mechanism (10.5/3.8, P10-S2). **Since Phase 59 Session 4**:
    gated via `_require_operator_key` - WHEN draining happens is decided
    by an external deploy tool/operator (`scripts/rolling-update.sh`), not
    by the registry itself or by the instance's own service identity, so
    this uses the same operator-bearer-secret shape as
    `federation-hub-service`'s `hub_operator_key`, not an `X-DMS-Principal`
    check."""
    _require_operator_key(authorization)
    try:
        result = await repository.mark_draining(session, instance_id)
    except repository.InstanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instance not registered") from exc
    await session.commit()
    result.license_status = await app.state.license_cache.status_for(result.service_type)
    return result


@app.post("/instances/{instance_id}/activate", response_model=InstanceOut)
async def activate_instance(
    instance_id: str,
    authorization: str = Header(default="", alias="Authorization"),
    session: AsyncSession = Depends(get_session),
) -> InstanceOut:
    """Reversal of `/drain` (10.5, P10-S3) - enables a genuine rollback path
    as long as the old instance has not yet been stopped. **Since Phase 59
    Session 4**: same `_require_operator_key` gate as `/drain` above."""
    _require_operator_key(authorization)
    try:
        result = await repository.activate(session, instance_id)
    except repository.InstanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instance not registered") from exc
    await session.commit()
    result.license_status = await app.state.license_cache.status_for(result.service_type)
    return result


@app.delete("/instances/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deregister_instance(
    instance_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Same self-service gate as `register_instance`/`send_heartbeat` above
    (Phase 59 Session 4) - same missing-header-collapses-into-403 reasoning
    as `send_heartbeat`."""
    try:
        deregistered = await repository.deregister(session, instance_id, x_dms_principal)
    except repository.InstanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instance not registered") from exc
    except repository.PermissionDeniedError as exc:
        raise HTTPException(
            status_code=403,
            detail="X-DMS-Principal muss dem service_type der Instanz entsprechen",
        ) from exc
    await session.commit()
    await publish_event(
        "registry.instance.deregistered",
        subject=instance_id,
        payload={"service_type": deregistered.service_type},
        actor=f"system:{deregistered.service_type}",
    )


async def _with_license_status(instances: list[InstanceOut]) -> list[InstanceOut]:
    for instance in instances:
        instance.license_status = await app.state.license_cache.status_for(instance.service_type)
    return instances


@app.get("/instances", response_model=list[InstanceOut])
async def list_instances(session: AsyncSession = Depends(get_session)) -> list[InstanceOut]:
    instances = await repository.list_all(
        session, heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds
    )
    return await _with_license_status(instances)


@app.get("/instances/{service_type}", response_model=list[InstanceOut])
async def list_active_instances(
    service_type: str, session: AsyncSession = Depends(get_session)
) -> list[InstanceOut]:
    instances = await repository.list_active_by_type(
        session, service_type, heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds
    )
    return await _with_license_status(instances)


@app.get("/license-status/{service_type}", response_model=LicenseStatusForServiceOut)
async def get_license_status(service_type: str) -> LicenseStatusForServiceOut:
    status_value = await app.state.license_cache.status_for(service_type)
    return LicenseStatusForServiceOut(service_type=service_type, status=status_value)
