import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_metrics_client import SensorConfigClient, metrics_payload, run_gauge_sampler_loop
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, Response, status
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

    # Sensor concept (10.1, P11-S1): registry-service is one of the two
    # pilots (see the P11-S0 finding). Activation status comes from
    # `monitoring-service`, not from its own DB - a symmetric pattern to
    # every other sensor-emitting service (no special case, even though this
    # service happens to also be the registry itself).
    app.state.sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await app.state.sensor_config_client.start()
    sensor_registry, active_gauge, heartbeat_miss_gauge = metrics.build_sensor_registry(
        app.state.sensor_config_client
    )
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

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    sensor_sampler_task.cancel()
    with suppress(asyncio.CancelledError):
        await sensor_sampler_task
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    await consumer_bus.close()
    await event_bus.close()
    await app.state.license_cache.close()
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


@app.post("/instances", response_model=InstanceOut, status_code=status.HTTP_201_CREATED)
async def register_instance(
    payload: RegisterRequest, session: AsyncSession = Depends(get_session)
) -> InstanceOut:
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
    instance_id: str, session: AsyncSession = Depends(get_session)
) -> InstanceOut:
    try:
        result = await repository.heartbeat(session, instance_id)
    except repository.InstanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instance not registered") from exc
    await session.commit()
    result.license_status = await app.state.license_cache.status_for(result.service_type)
    return result


@app.post("/instances/{instance_id}/drain", response_model=InstanceOut)
async def drain_instance(
    instance_id: str, session: AsyncSession = Depends(get_session)
) -> InstanceOut:
    """Drain mechanism (10.5/3.8, P10-S2) - ungated like every other registry
    endpoint: WHEN draining happens is decided by an external deploy
    tool/P10-S3, not by the registry itself."""
    try:
        result = await repository.mark_draining(session, instance_id)
    except repository.InstanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instance not registered") from exc
    await session.commit()
    result.license_status = await app.state.license_cache.status_for(result.service_type)
    return result


@app.post("/instances/{instance_id}/activate", response_model=InstanceOut)
async def activate_instance(
    instance_id: str, session: AsyncSession = Depends(get_session)
) -> InstanceOut:
    """Reversal of `/drain` (10.5, P10-S3) - enables a genuine rollback path
    as long as the old instance has not yet been stopped."""
    try:
        result = await repository.activate(session, instance_id)
    except repository.InstanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instance not registered") from exc
    await session.commit()
    result.license_status = await app.state.license_cache.status_for(result.service_type)
    return result


@app.delete("/instances/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deregister_instance(
    instance_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        deregistered = await repository.deregister(session, instance_id)
    except repository.InstanceNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Instance not registered") from exc
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
