import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service import repository
from notification_service.auth_client import AuthServiceClient
from notification_service.consumer import publish_notification_result, start_consuming
from notification_service.models import Base, Notification
from notification_service.schemas import NotificationCreate, NotificationOut
from notification_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def _run_retry_tick(session_factory) -> None:
    """A single pass of the due retry attempts - factored out
    of `_notification_retry_poll_loop` so that a tick is independently testable
    (same pattern as archival-service's `run_active_transfers_tick`)."""
    async with session_factory() as session:
        due = await repository.list_due_for_retry(session)
    for stale in due:
        async with session_factory() as session:
            notification = await session.get(Notification, stale.id)
            if notification is None or notification.status != "failed":
                continue  # handled differently in the meantime (e.g. manual retry)
            await repository.attempt_delivery(
                session, settings, notification, max_attempts=settings.max_notification_attempts
            )
            await session.commit()
            await publish_notification_result(publish_event, notification)


async def _notification_retry_poll_loop(session_factory) -> None:
    """Retries failed email/webhook deliveries (post-roadmap
    Phase 20 Session 3, ADR 0079) - the first delivery attempt deliberately
    remains synchronous in the NATS handler resp. in `POST /notifications` (fast
    response in the normal case), only the RETRY runs asynchronously in this
    own poll loop, so as not to block the handler. Same idiom as
    archival-service's `_archival_poll_loop`."""
    while True:
        try:
            await _run_retry_tick(session_factory)
        except Exception:
            logger.exception(
                "Notification-Retry-Poll-Tick fehlgeschlagen - "
                "wird beim naechsten Tick erneut versucht."
            )
        await asyncio.sleep(settings.notification_retry_poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS notification"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc migration for already running installations (post-roadmap
        # Phase 20 Session 3, ADR 0079) - `create_all` only creates new tables,
        # but does not alter existing ones (same pattern as archival-service).
        await conn.execute(
            text(
                "ALTER TABLE notification.notification "
                "ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE notification.notification "
                "ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.auth_client = AuthServiceClient(
        settings.auth_service_base_url,
        admin_username=settings.auth_service_admin_username,
        admin_password=settings.auth_service_admin_password,
    )

    # Producer (own stream "notification", `notification.sent`/`.failed`) AND
    # consumer (`workflow.task.escalated`) - two separate client instances, as
    # noted in docs/services/workflow-service.md as the convention for services
    # with both roles (audit-service has so far been a pure consumer, workflow-service
    # a pure producer - here both are actually needed for the first time).
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

    retry_poll_task = asyncio.create_task(_notification_retry_poll_loop(app.state.session_factory))

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    retry_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await retry_poll_task
    if registration:
        await registration.stop()
    await consumer.close()
    await producer.close()
    await app.state.auth_client.close()
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
    await app.state.producer.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/notifications", response_model=NotificationOut, status_code=status.HTTP_201_CREATED)
async def create_notification(
    payload: NotificationCreate, session: AsyncSession = Depends(get_session)
) -> NotificationOut:
    """Retrofit P6-S6 (call authorization): since this session, the public
    endpoint checks that `recipient` for `channel in {"email","in_app"}` is
    a real `auth-service` account, instead of accepting it blindly -
    `channel="webhook"` remains unchecked (the target is a URL, not an identity).
    The internal alerting path (SLA/break-glass/emergency-shutdown) never runs
    through this endpoint, see `auth_client.py`."""
    if not await app.state.auth_client.recipient_exists(payload.recipient, channel=payload.channel):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unbekannter Empfänger {payload.recipient!r} für Kanal {payload.channel!r}",
        )
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


@app.post("/notifications/{notification_id}/retry", response_model=NotificationOut)
async def retry_notification(
    notification_id: int, session: AsyncSession = Depends(get_session)
) -> NotificationOut:
    """Manual restart of a permanently failed delivery
    (post-roadmap Phase 20 Session 3, ADR 0079) - only useful for
    `failed_permanent` (409 otherwise); immediately makes a new synchronous
    delivery attempt instead of waiting for the next poll tick."""
    try:
        notification = await repository.get_notification(session, notification_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if notification.status != "failed_permanent":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Notification hat Status {notification.status!r}, nur "
                "'failed_permanent' kann erneut versucht werden"
            ),
        )
    await repository.retry_now(
        session, settings, notification, max_attempts=settings.max_notification_attempts
    )
    await session.commit()
    await publish_notification_result(publish_event, notification)
    return notification
