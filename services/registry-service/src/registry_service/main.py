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
        # Kein Alembic in dieser frühen Phase - Schema/Tabellen werden beim
        # Start idempotent sichergestellt. Migrationen folgen, sobald sich
        # das Modell in Produktion stabilisiert hat.
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS registry"))
        await conn.run_sync(Base.metadata.create_all)
        # `create_all` legt fehlende TABELLEN an, aendert aber keine
        # bestehenden - `status` (Drain-Mechanismus, 10.5/3.8, P10-S2) kam
        # erst nachtraeglich dazu, gleiches additive Ad-hoc-Migrationsmuster
        # wie z. B. document-service. Idempotent dank IF NOT EXISTS.
        await conn.execute(
            text(
                "ALTER TABLE registry.service_instance "
                "ADD COLUMN IF NOT EXISTS status VARCHAR(16) DEFAULT 'active' NOT NULL"
            )
        )
        # Sensor-Katalog (10.1, P11-S1) - gleiches additive Muster.
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

    # Lizenzvermittlung (Konzept 3.2b/9.3, P9-S2): erster eigener NATS-
    # Konsument der Registry - reagiert auf Statusaenderungen des
    # license-service (P9-S1) durch Invalidierung des TTL-Caches, statt
    # ausschliesslich zeitbasiert neu abzufragen.
    app.state.license_cache = ComponentLicenseCache(
        LicenseServiceClient(settings.license_service_base_url),
        licensable_components=settings.licensable_components,
        cache_ttl_seconds=settings.license_status_cache_ttl_seconds,
    )
    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    await consumer.start_consuming(consumer_bus, ["license.>"], app.state.license_cache)

    # Die Registry meldet sich seit P4-S3 bei sich selbst an (registry_service_base_url
    # zeigt auf die eigene Adresse) - Grundlage dafür, dass das Gateway auch
    # "registry-service" als service_type auflösen kann (z. B. für die
    # Admin-UI-Registry-Übersicht, die konsequent nur über das Gateway spricht,
    # nie direkt). Die allererste Registrierung schlägt unvermeidlich fehl (der
    # eigene Uvicorn-Server nimmt erst nach Abschluss des Lifespan-Startups
    # Verbindungen an) - der Selbstheilungs-Fix aus `dms-registry-client`
    # (Re-Registrierung bei 404 im nächsten Heartbeat) greift hier also für
    # den denkbar häufigsten Fall: Selbstregistrierung beim eigenen Start.
    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        sensors=metrics.sensor_declarations(),
    )

    # Sensor-Konzept (10.1, P11-S1): registry-service ist einer der zwei
    # Piloten (siehe P11-S0-Befund). Aktivierungsstatus kommt vom
    # `monitoring-service`, nicht aus der eigenen DB - symmetrisches Muster
    # zu jedem anderen Sensor-emittierenden Service (kein Sonderfall, obwohl
    # dieser Service zufaellig auch die Registry selbst ist).
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


@app.get("/metrics")
def get_metrics() -> Response:
    """Prometheus-Exposition der zwei eigenen Sensoren (10.1, P11-S1) - wird
    vom `monitoring-service` gescraped, nicht direkt von Prometheus."""
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
    """Drain-Mechanismus (10.5/3.8, P10-S2) - ungegatet wie jeder andere
    Registry-Endpunkt: WANN gedraint wird, entscheidet ein externes
    Deploy-Werkzeug/P10-S3, nicht die Registry selbst."""
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
    """Umkehrung von `/drain` (10.5, P10-S3) - ermoeglicht einen echten
    Rollback-Pfad, solange die alte Instanz noch nicht gestoppt wurde."""
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
