import os

import pytest
from case_service.models import Base
from dms_db_base import build_engine, make_session_factory
from sqlalchemy import text

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

WORKFLOW_SERVICE_URL = os.environ.get("TEST_WORKFLOW_SERVICE_URL", "http://localhost:8014")
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
OBJECT_TYPE_SERVICE_URL = os.environ.get("TEST_OBJECT_TYPE_SERVICE_URL", "http://localhost:8007")
os.environ["DMS_WORKFLOW_SERVICE_BASE_URL"] = WORKFLOW_SERVICE_URL
os.environ["DMS_DOCUMENT_SERVICE_BASE_URL"] = DOCUMENT_SERVICE_URL
os.environ["DMS_OBJECT_TYPE_SERVICE_BASE_URL"] = OBJECT_TYPE_SERVICE_URL


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        # "case" ist ein reserviertes SQL-Schluesselwort - rohe SQL-Strings
        # muessen es selbst quoten (siehe models.py-Kommentar).
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS "case"'))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text('TRUNCATE "case".case_document_reference, "case".cases CASCADE'))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS "case"'))
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
    """Prozess mit paralleler Script-/Manual-Task (aus dem offiziellen
    sartography/SpiffWorkflow-Repo, bereits als workflow-service-Fixture
    verifiziert) - erreicht den Endzustand erst nach Abschluss der
    Manual Task, geeignet um `workflow.instance.completed` gezielt nach dem
    Anlegen der Referenzen auszuloesen statt bereits beim Instanzstart."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "script_and_manual.bpmn")
    with open(path, encoding="utf-8") as f:
        return f.read()
