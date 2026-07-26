import os

import pytest
from audit_service.models import Base
from dms_db_base import build_engine, make_session_factory
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)


@pytest.fixture(autouse=True)
async def _clean_audit_table():
    """Räumt VOR jedem Test auf, unabhängig davon, welche Fixtures er nutzt -
    nötig, weil test_consumer_integration.py über die App/`main.py` eigene
    Engine-Verbindungen aufbaut, nicht über die `engine`/`session`-Fixtures
    unten, und sonst Zeilen zwischen Testdateien durchsickern würden.
    """
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS audit"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DELETE FROM audit.audit_event"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS audit"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
