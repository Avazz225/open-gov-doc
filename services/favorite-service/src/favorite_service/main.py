import logging
import time
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
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from favorite_service import repository
from favorite_service.models import Base
from favorite_service.schemas import FavoriteCreate, FavoriteOut
from favorite_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS favorite"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    event_bus = NatsEventBusClient(settings.nats_url, stream="favorite")
    await event_bus.connect()
    app.state.event_bus = event_bus

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
    await event_bus.close()
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
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


@app.post("/favorites", response_model=FavoriteOut, status_code=status.HTTP_201_CREATED)
async def create_favorite(
    payload: FavoriteCreate, session: AsyncSession = Depends(get_session)
) -> FavoriteOut:
    try:
        favorite = await repository.create_favorite(session, payload)
    except repository.DuplicateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "favorite.added",
        subject=favorite.id,
        payload={
            "user_id": favorite.user_id,
            "object_type": favorite.object_type,
            "object_id": favorite.object_id,
        },
        actor=favorite.user_id,
    )
    return favorite


@app.get("/favorites", response_model=list[FavoriteOut])
async def list_favorites(
    user_id: str, object_type: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[FavoriteOut]:
    return await repository.list_favorites(session, user_id=user_id, object_type=object_type)


@app.delete("/favorites", status_code=status.HTTP_204_NO_CONTENT)
async def delete_favorite(
    user_id: str,
    object_type: str,
    object_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await repository.delete_favorite(
            session, user_id=user_id, object_type=object_type, object_id=object_id
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "favorite.removed",
        subject=object_id,
        payload={"user_id": user_id, "object_type": object_type, "object_id": object_id},
        actor=user_id,
    )
