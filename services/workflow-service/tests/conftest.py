import os

import pytest
from dms_db_base import build_engine, make_session_factory
from sqlalchemy import text
from workflow_service.models import Base

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings (liest DMS_POSTGRES_DSN) wie für die
# Test-Fixtures oben - sonst testet TestClient(app) unbemerkt gegen die Live-DB,
# siehe PROGRESS.md "Tooling & Testing".
os.environ["DMS_POSTGRES_DSN"] = DSN
NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")
os.environ["DMS_NATS_URL"] = NATS_URL


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS workflow"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("TRUNCATE workflow.process_instance, workflow.process_definition CASCADE")
        )
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS workflow"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s


@pytest.fixture
def manual_task_bpmn() -> str:
    path = os.path.join(os.path.dirname(__file__), "fixtures", "script_and_manual.bpmn")
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def no_tasks_bpmn() -> str:
    path = os.path.join(os.path.dirname(__file__), "fixtures", "no_tasks.bpmn")
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def lanes_bpmn() -> str:
    path = os.path.join(os.path.dirname(__file__), "fixtures", "lanes.bpmn")
    with open(path, encoding="utf-8") as f:
        return f.read()
