import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from audit_service import repository
from audit_service.consumer import start_consuming
from audit_service.models import Base
from audit_service.schemas import AuditEventOut, ChainVerificationOut
from audit_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS audit"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc-Schema-Erweiterung (kein Alembic, siehe CONTRIBUTING.md) -
        # first-class Actor-Feld seit P7-S2.
        await conn.execute(
            text("ALTER TABLE audit.audit_event ADD COLUMN IF NOT EXISTS actor VARCHAR(255)")
        )
        # Stellvertretung bei Abwesenheit (4.4a, seit P14-S11) - beide Spalten
        # bewusst OHNE NOT NULL/DEFAULT-Klausel: `on_behalf_of_field_cutover_id`
        # bleibt auf einer bereits bestehenden Zeile NULL, bis
        # `get_on_behalf_of_field_cutover_id` sie einmalig auf den korrekten
        # MAX(id)-Wert setzt (siehe repository.py).
        await conn.execute(
            text("ALTER TABLE audit.audit_event ADD COLUMN IF NOT EXISTS on_behalf_of VARCHAR(255)")
        )
        await conn.execute(
            text(
                "ALTER TABLE audit.audit_meta "
                "ADD COLUMN IF NOT EXISTS on_behalf_of_field_cutover_id INTEGER"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    # Cutover-Punkte fuer die actor-/on_behalf_of-Felder einmalig festlegen
    # (P7-S2/P14-S11) - muss vor dem ersten konsumierten Event geschehen,
    # sonst koennte ein frisches Event faelschlich als "vor dem Cutover"
    # behandelt werden.
    async with app.state.session_factory() as session:
        await repository.get_actor_field_cutover_id(session)
        await repository.get_on_behalf_of_field_cutover_id(session)
        await session.commit()

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    event_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await event_bus.connect()
    app.state.event_bus = event_bus
    await start_consuming(
        event_bus,
        settings.subjects,
        app.state.session_factory,
        Path(settings.deletion_ledger_path),
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


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


@app.get("/events", response_model=list[AuditEventOut])
async def list_events(
    limit: int = 100,
    actor: str | None = None,
    on_behalf_of: str | None = None,
    subject: str | None = None,
    event_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[AuditEventOut]:
    return await repository.list_events(
        session,
        limit=limit,
        actor=actor,
        on_behalf_of=on_behalf_of,
        subject=subject,
        event_type=event_type,
        since=since,
        until=until,
    )


@app.get("/events/verify", response_model=ChainVerificationOut)
async def verify_chain(session: AsyncSession = Depends(get_session)) -> ChainVerificationOut:
    result = await repository.verify_chain(session)
    return ChainVerificationOut(
        ok=result.ok, checked=result.checked, broken_at_id=result.broken_at_id
    )
