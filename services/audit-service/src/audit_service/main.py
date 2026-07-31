import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI
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
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    event_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await event_bus.connect()
    app.state.event_bus = event_bus
    await start_consuming(event_bus, settings.subjects, app.state.session_factory)

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


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/events", response_model=list[AuditEventOut])
async def list_events(
    limit: int = 100, session: AsyncSession = Depends(get_session)
) -> list[AuditEventOut]:
    return await repository.list_events(session, limit=limit)


@app.get("/events/verify", response_model=ChainVerificationOut)
async def verify_chain(session: AsyncSession = Depends(get_session)) -> ChainVerificationOut:
    result = await repository.verify_chain(session)
    return ChainVerificationOut(
        ok=result.ok, checked=result.checked, broken_at_id=result.broken_at_id
    )
