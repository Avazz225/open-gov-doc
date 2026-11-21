import asyncio
import contextlib
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
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from favorite_service import reminders, repository
from favorite_service.models import Base, Reminder
from favorite_service.schemas import (
    FavoriteCreate,
    FavoriteOut,
    ReminderCreate,
    ReminderOut,
    ReminderSnooze,
    ReminderUpdate,
)
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

    # Follow-up reminders (P90-S1, ADR 0246).
    reminder_task = asyncio.create_task(_reminder_poll_loop())

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    reminder_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await reminder_task
    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    await event_bus.close()
    await engine.dispose()


async def run_reminder_tick() -> int | None:
    """One due poll (P90-S1): claims due reminders under the advisory lock,
    commits, then publishes `favorite.reminder.due` per reminder for
    notification-service. Returns how many were claimed (None = another
    replica had the lock)."""
    async with app.state.session_factory() as session:
        claimed = await reminders.claim_due(session, now=reminders.utcnow())
        await session.commit()
    if not claimed:
        return None if claimed is None else 0
    for due in claimed:
        await publish_event(
            "favorite.reminder.due",
            subject=due.id,
            payload={
                "user_id": due.user_id,
                "object_type": due.object_type,
                "object_id": due.object_id,
                "due_at": due.due_at.isoformat(),
                "note": due.note,
            },
            actor="system:favorite-reminders",
        )
    return len(claimed)


async def _reminder_poll_loop() -> None:
    await asyncio.sleep(settings.reminder_poll_initial_delay_seconds)
    while True:
        try:
            await run_reminder_tick()
        except Exception:
            logger.exception("reminder_tick_failed")
        await asyncio.sleep(settings.reminder_poll_interval_seconds)


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


def _require_own_user_id(user_id: str, x_dms_username: str) -> None:
    """P54-S3: `user_id` was previously a fully client-supplied field/query
    param with no check at all against the caller's own identity - any
    authenticated user could view/add/delete any OTHER user's favorites by
    passing a different `user_id` (confirmed via grep: this service never
    read any `X-DMS-*` identity header anywhere before this session). Same
    "you may only act as yourself" principle `document-service`'s own
    `scope="personal"` path already uses elsewhere.

    Deliberately checked against the gateway-verified `X-DMS-Username`
    (Keycloak `preferred_username`), NOT `X-DMS-Principal` (Keycloak `sub`,
    a UUID): this service's `user_id` values have always been
    username-based in practice - `apps/user-ui`'s only real caller
    (`CasesPane.tsx`) already sends `useAuth().user.username`, never `sub`.
    Checking against `X-DMS-Principal` instead would have rejected every
    real, already-existing favorites call in production. `X-DMS-Username`
    is set by the gateway from the same verified JWT claims, via the same
    unconditional `.update(identity_headers)` as `X-DMS-Principal` - equally
    unspoofable for a real end user, just the identifier scheme this
    service's own data actually uses."""
    if not x_dms_username:
        raise HTTPException(status_code=401, detail="X-DMS-Username fehlt")
    if user_id != x_dms_username:
        raise HTTPException(
            status_code=403, detail="user_id muss dem eigenen X-DMS-Username entsprechen"
        )


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
    payload: FavoriteCreate,
    session: AsyncSession = Depends(get_session),
    x_dms_username: str = Header(default=""),
) -> FavoriteOut:
    _require_own_user_id(payload.user_id, x_dms_username)
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
    user_id: str,
    object_type: str | None = None,
    session: AsyncSession = Depends(get_session),
    x_dms_username: str = Header(default=""),
) -> list[FavoriteOut]:
    _require_own_user_id(user_id, x_dms_username)
    return await repository.list_favorites(session, user_id=user_id, object_type=object_type)


@app.delete("/favorites", status_code=status.HTTP_204_NO_CONTENT)
async def delete_favorite(
    user_id: str,
    object_type: str,
    object_id: str,
    session: AsyncSession = Depends(get_session),
    x_dms_username: str = Header(default=""),
) -> None:
    _require_own_user_id(user_id, x_dms_username)
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


# --- Follow-up reminders (Wiedervorlagen, P90-S1, ADR 0246) -----------------


def _reminder_out(reminder: Reminder, now) -> ReminderOut:
    out = ReminderOut.model_validate(reminder)
    out.is_due = reminder.status == "open" and reminder.due_at <= now
    return out


async def _publish_reminder(event_type: str, reminder: Reminder) -> None:
    await publish_event(
        event_type,
        subject=reminder.id,
        payload={
            "user_id": reminder.user_id,
            "object_type": reminder.object_type,
            "object_id": reminder.object_id,
            "due_at": reminder.due_at.isoformat(),
        },
        actor=reminder.user_id,
    )


@app.post("/reminders", response_model=ReminderOut, status_code=status.HTTP_201_CREATED)
async def create_reminder(
    payload: ReminderCreate,
    session: AsyncSession = Depends(get_session),
    x_dms_username: str = Header(default=""),
) -> ReminderOut:
    """A due time in the past is allowed: it is due at once (and notified on
    the next poll), which is what "remind me now" means."""
    _require_own_user_id(payload.user_id, x_dms_username)
    now = reminders.utcnow()
    reminder = await reminders.create_reminder(session, payload, now=now)
    await session.commit()
    await _publish_reminder("favorite.reminder.created", reminder)
    return _reminder_out(reminder, now)


@app.get("/reminders", response_model=list[ReminderOut])
async def list_reminders(
    user_id: str,
    status: str | None = None,
    due: bool = False,
    object_type: str | None = None,
    object_id: str | None = None,
    session: AsyncSession = Depends(get_session),
    x_dms_username: str = Header(default=""),
) -> list[ReminderOut]:
    """The caller's own reminders, earliest due first. `due=true`: only open
    ones whose time has come (the "Wiedervorlagen" view); `object_type`/
    `object_id`: those on one object (the detail pane)."""
    _require_own_user_id(user_id, x_dms_username)
    if status is not None and status not in ("open", "done"):
        raise HTTPException(status_code=422, detail="status muss 'open' oder 'done' sein")
    now = reminders.utcnow()
    rows = await reminders.list_reminders(
        session,
        user_id=user_id,
        status=status,
        due_only=due,
        object_type=object_type,
        object_id=object_id,
        now=now,
    )
    return [_reminder_out(r, now) for r in rows]


@app.get("/reminders/due-count")
async def count_due_reminders(
    user_id: str,
    session: AsyncSession = Depends(get_session),
    x_dms_username: str = Header(default=""),
) -> dict:
    """For a badge in the user-ui (P90-S2): open reminders whose time has come."""
    _require_own_user_id(user_id, x_dms_username)
    return {"due": await reminders.count_due(session, user_id=user_id, now=reminders.utcnow())}


async def _own_reminder(session: AsyncSession, reminder_id: str, x_dms_username: str) -> Reminder:
    if not x_dms_username:
        raise HTTPException(status_code=401, detail="X-DMS-Username fehlt")
    try:
        return await reminders.get_own_reminder(session, reminder_id, user_id=x_dms_username)
    except reminders.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/reminders/{reminder_id}", response_model=ReminderOut)
async def update_reminder(
    reminder_id: str,
    payload: ReminderUpdate,
    session: AsyncSession = Depends(get_session),
    x_dms_username: str = Header(default=""),
) -> ReminderOut:
    reminder = await _own_reminder(session, reminder_id, x_dms_username)
    now = reminders.utcnow()
    try:
        if payload.note is not None:
            reminders.update_note(reminder, payload.note, now=now)
        if payload.due_at is not None:
            reminders.reschedule(reminder, payload.due_at, now=now)
    except reminders.NotOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await _publish_reminder("favorite.reminder.updated", reminder)
    return _reminder_out(reminder, now)


@app.post("/reminders/{reminder_id}/snooze", response_model=ReminderOut)
async def snooze_reminder(
    reminder_id: str,
    payload: ReminderSnooze,
    session: AsyncSession = Depends(get_session),
    x_dms_username: str = Header(default=""),
) -> ReminderOut:
    """ "Später erinnern": a new due time in the future; the notification
    fires again then."""
    reminder = await _own_reminder(session, reminder_id, x_dms_username)
    now = reminders.utcnow()
    if payload.until <= now:
        raise HTTPException(status_code=422, detail="until muss in der Zukunft liegen")
    try:
        reminders.reschedule(reminder, payload.until, now=now)
    except reminders.NotOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await _publish_reminder("favorite.reminder.snoozed", reminder)
    return _reminder_out(reminder, now)


@app.post("/reminders/{reminder_id}/done", response_model=ReminderOut)
async def complete_reminder(
    reminder_id: str,
    session: AsyncSession = Depends(get_session),
    x_dms_username: str = Header(default=""),
) -> ReminderOut:
    reminder = await _own_reminder(session, reminder_id, x_dms_username)
    now = reminders.utcnow()
    try:
        reminders.complete(reminder, now=now)
    except reminders.NotOpenError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await _publish_reminder("favorite.reminder.completed", reminder)
    return _reminder_out(reminder, now)


@app.post("/reminders/{reminder_id}/reopen", response_model=ReminderOut)
async def reopen_reminder(
    reminder_id: str,
    session: AsyncSession = Depends(get_session),
    x_dms_username: str = Header(default=""),
) -> ReminderOut:
    """Undo "done" - for a misclick in the list."""
    reminder = await _own_reminder(session, reminder_id, x_dms_username)
    now = reminders.utcnow()
    reminders.reopen(reminder, now=now)
    await session.commit()
    return _reminder_out(reminder, now)


@app.delete("/reminders/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_reminder(
    reminder_id: str,
    session: AsyncSession = Depends(get_session),
    x_dms_username: str = Header(default=""),
) -> None:
    reminder = await _own_reminder(session, reminder_id, x_dms_username)
    await session.delete(reminder)
    await session.commit()
    await _publish_reminder("favorite.reminder.deleted", reminder)
