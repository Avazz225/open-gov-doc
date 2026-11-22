import os

import pytest
from dms_db_base import build_engine, make_session_factory
from favorite_service.models import Base
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings (liest DMS_POSTGRES_DSN) wie für die
# Test-Fixtures oben - sonst testet TestClient(app) unbemerkt gegen die Live-DB,
# siehe PROGRESS.md "Tooling & Testing" (P5-S2-Datenverlust, P5b-S6-Leck).
os.environ["DMS_POSTGRES_DSN"] = DSN
# P90-S1: the reminder poll must not race the tests that drive
# `run_reminder_tick` themselves.
os.environ["DMS_REMINDER_POLL_INITIAL_DELAY_SECONDS"] = "3600"
# P90-S4: tests drive the watch matching directly; the durable consumers of a
# running stack stay untouched.
os.environ["DMS_WATCH_CONSUMER_ENABLED"] = "false"


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS favorite"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE favorite.favorite CASCADE"))
        await conn.execute(text("TRUNCATE favorite.reminder"))
        await conn.execute(text("TRUNCATE favorite.watch"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS favorite"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
