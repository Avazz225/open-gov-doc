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

from notification_service import repository
from notification_service.consumer import publish_notification_result, start_consuming
from notification_service.models import Base
from notification_service.schemas import NotificationCreate, NotificationOut
from notification_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS notification"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    # Producer (eigener Stream "notification", `notification.sent`/`.failed`) UND
    # Konsument (`workflow.task.escalated`) - zwei getrennte Client-Instanzen, wie in
    # docs/services/workflow-service.md als Konvention für Services mit beiden Rollen
    # notiert (audit-service ist bisher reiner Konsument, workflow-service reiner
    # Producer - hier zum ersten Mal beides tatsächlich gebraucht).
    producer = NatsEventBusClient(settings.nats_url, stream="notification")
    await producer.connect()
    app.state.producer = producer

    consumer = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer.connect()
    app.state.consumer = consumer
    await start_consuming(
        consumer, settings.subjects, app.state.session_factory, settings, publish_event
    )

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
    await consumer.close()
    await producer.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(event_type: str, subject: str, payload: dict) -> None:
    event = Event(
        event_type=event_type, service_name=settings.service_name, subject=subject, payload=payload
    )
    await app.state.producer.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/notifications", response_model=NotificationOut, status_code=status.HTTP_201_CREATED)
async def create_notification(
    payload: NotificationCreate, session: AsyncSession = Depends(get_session)
) -> NotificationOut:
    notification = await repository.create_and_send(
        session,
        settings,
        channel=payload.channel,
        recipient=payload.recipient,
        subject=payload.subject,
        body=payload.body,
    )
    await session.commit()
    await publish_notification_result(publish_event, notification)
    return notification


@app.get("/notifications", response_model=list[NotificationOut])
async def list_notifications(
    recipient: str | None = None,
    channel: str | None = None,
    status: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[NotificationOut]:
    return await repository.list_notifications(
        session, recipient=recipient, channel=channel, status=status
    )


@app.get("/notifications/{notification_id}", response_model=NotificationOut)
async def get_notification(
    notification_id: int, session: AsyncSession = Depends(get_session)
) -> NotificationOut:
    try:
        return await repository.get_notification(session, notification_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
