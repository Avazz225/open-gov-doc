import logging
import time

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from registry_service import repository
from registry_service.models import Base
from registry_service.schemas import InstanceOut, RegisterRequest
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
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    event_bus = NatsEventBusClient(settings.nats_url, stream="registry")
    await event_bus.connect()
    app.state.event_bus = event_bus

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
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    if registration:
        await registration.stop()
    await event_bus.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(event_type: str, subject: str, payload: dict) -> None:
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/instances", response_model=InstanceOut, status_code=status.HTTP_201_CREATED)
async def register_instance(
    payload: RegisterRequest, session: AsyncSession = Depends(get_session)
) -> InstanceOut:
    result = await repository.register(session, payload)
    await session.commit()
    await publish_event(
        "registry.instance.registered",
        subject=payload.instance_id,
        payload={"service_type": payload.service_type, "version": payload.version},
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
    )


@app.get("/instances", response_model=list[InstanceOut])
async def list_instances(session: AsyncSession = Depends(get_session)) -> list[InstanceOut]:
    return await repository.list_all(
        session, heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds
    )


@app.get("/instances/{service_type}", response_model=list[InstanceOut])
async def list_active_instances(
    service_type: str, session: AsyncSession = Depends(get_session)
) -> list[InstanceOut]:
    return await repository.list_active_by_type(
        session, service_type, heartbeat_timeout_seconds=settings.heartbeat_timeout_seconds
    )
