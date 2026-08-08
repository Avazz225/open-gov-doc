import os

import pytest
from dms_db_base import build_engine, make_session_factory
from plugin_orchestration_service.models import Base
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
os.environ["DMS_POSTGRES_DSN"] = DSN


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS orchestration"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE orchestration.placement_decision"))
        await conn.execute(text("TRUNCATE orchestration.plugin_resource_report"))
        await conn.execute(text("TRUNCATE orchestration.cluster_node"))
        await conn.execute(text("TRUNCATE orchestration.plugin_manifest"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS orchestration"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
