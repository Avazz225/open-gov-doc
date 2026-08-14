import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from monitoring_service import repository, scraper
from monitoring_service.clients import AuthServiceClient, PermissionServiceClient, RegistryClient
from monitoring_service.models import Base
from monitoring_service.schemas import (
    GlobalSensorConfigIn,
    SensorConfigOut,
    SensorOut,
    SensorOverrideIn,
)
from monitoring_service.settings import Settings
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def publish_event(event_type: str, subject: str | None, payload: dict) -> None:
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


async def _is_active_superuser(x_dms_principal: str) -> bool:
    active, superuser_principal_id = await app.state.auth_client.get_active_superuser()
    return active and bool(x_dms_principal) and superuser_principal_id == x_dms_principal


async def _require_monitoring_permission(x_dms_principal: str) -> None:
    """Enforces the new domain-separated admin role
    `domain-admin-monitoring` (`admin.monitoring`, P11-S1) - the activated
    superuser (4.6) is the only exception, same gate pattern as
    license-service/plugin-orchestration-service. Concept 10.1: disabling
    security-relevant sensors is itself a security-relevant,
    audited operation."""
    if await _is_active_superuser(x_dms_principal):
        return
    allowed = bool(x_dms_principal) and await app.state.permission_client.has_permission(
        x_dms_principal, settings.monitoring_permission
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Monitoring-/Sensor-Konfiguration'",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()

    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS monitoring"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    async with app.state.session_factory() as session:
        await repository.ensure_global_default_seeded(session)
        await session.commit()

    app.state.auth_client = AuthServiceClient(settings.auth_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.registry_client = RegistryClient(settings.registry_service_base_url)
    app.state.scrape_client = httpx.AsyncClient()

    # Always-active, non-configurable operational counter (P11-S1
    # decision: a self-reference - monitoring-service asking itself
    # via HTTP whether this very sensor is active - would be unnecessary
    # complexity without real benefit). Own CollectorRegistry, separate
    # from the scraped domain service metrics.
    app.state.own_registry = CollectorRegistry()
    app.state.scrape_failures_counter = Counter(
        "monitoring_scrape_failures_total",
        "Anzahl fehlgeschlagener Scrape-Versuche gegen Fach-Service-/metrics-Endpunkte",
        registry=app.state.own_registry,
    )

    event_bus = NatsEventBusClient(settings.nats_url, stream="monitoring")
    await event_bus.connect()
    app.state.event_bus = event_bus

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis)

    yield

    if registration:
        await registration.stop()
    await event_bus.close()
    await app.state.scrape_client.aclose()
    await app.state.registry_client.close()
    await app.state.auth_client.close()
    await app.state.permission_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


class _StaticMetricsCollector:
    """Passes an already fully merged list of `Metric` objects
    unchanged to `generate_latest()` - `scraper.merge_metric_families`
    delivers this list freshly per request (10.1: live scrape instead
    of a cached snapshot)."""

    def __init__(self, metrics: list) -> None:
        self._metrics = metrics

    def collect(self):
        yield from self._metrics


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
async def get_metrics() -> Response:
    """Scrape proxy (10.1, P11-S1): the only target that Prometheus
    actually queries. Fetches the /metrics endpoints of all currently
    active, healthy instances live and in parallel (address list comes
    from `registry-service`) and merges them into a single response."""
    instances = await app.state.registry_client.list_active_instances()
    bodies = await scraper.scrape_targets(
        app.state.scrape_client,
        instances,
        timeout_seconds=settings.scrape_timeout_seconds,
        on_failure=app.state.scrape_failures_counter.inc,
    )
    merged = scraper.merge_metric_families(bodies)

    combined_registry = CollectorRegistry()
    combined_registry.register(_StaticMetricsCollector(merged))
    body = generate_latest(combined_registry) + generate_latest(app.state.own_registry)
    return Response(content=body, media_type=CONTENT_TYPE_LATEST)


@app.get("/sensors", response_model=list[SensorOut])
async def list_sensors(session: AsyncSession = Depends(get_session)) -> list[SensorOut]:
    instances = await app.state.registry_client.list_active_instances()
    return await repository.list_sensors(session, instances)


@app.get("/sensor-config", response_model=SensorConfigOut)
async def get_sensor_config(session: AsyncSession = Depends(get_session)) -> SensorConfigOut:
    """Ungated, polled by `dms_metrics_client.SensorConfigClient` -
    pure read access to information already publicly visible via
    `GET /sensors`, no role check needed."""
    return await repository.get_sensor_config(session)


@app.put("/sensor-config/global", response_model=SensorConfigOut)
async def set_global_sensor_config(
    payload: GlobalSensorConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> SensorConfigOut:
    await _require_monitoring_permission(x_dms_principal)
    result = await repository.set_global_default(session, payload.enabled)
    await session.commit()
    await publish_event(
        "monitoring.sensor.config_changed",
        subject="__global__",
        payload={"enabled": payload.enabled, "actor": x_dms_principal},
    )
    return result


@app.put("/sensor-config/{sensor_name}", response_model=SensorConfigOut)
async def set_sensor_override(
    sensor_name: str,
    payload: SensorOverrideIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> SensorConfigOut:
    await _require_monitoring_permission(x_dms_principal)
    result = await repository.set_sensor_override(session, sensor_name, payload.enabled)
    await session.commit()
    await publish_event(
        "monitoring.sensor.config_changed",
        subject=sensor_name,
        payload={"enabled": payload.enabled, "actor": x_dms_principal},
    )
    return result
