import os

import httpx
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

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
# Fester Principal statt uuid4() je Testlauf, damit die Rollenzuweisung
# idempotent bleibt (Unique-Constraint auf permission-service-Seite) - echter
# Aufruf gegen den laufenden permission-service (kein Mocking, P6-S6-Retrofit
# für Prozessdefinitionen/Script-Task-Upload, Capability `admin.object_config`).
CONFIG_ADMIN_PRINCIPAL_ID = "workflow-test-config-admin"


@pytest.fixture
def admin_headers() -> dict[str, str]:
    """Fixture statt Modul-Konstanten-Import (siehe PROGRESS.md "Tooling &
    Testing": `from conftest import x` funktioniert mit `--import-mode=importlib`
    nicht zuverlässig über Test-Module hinweg)."""
    return {"X-DMS-Principal": CONFIG_ADMIN_PRINCIPAL_ID}


@pytest.fixture(scope="session", autouse=True)
async def _grant_config_admin_permission():
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as client:
        roles = (await client.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-config")
        existing = (
            await client.get(
                "/role-assignments", params={"principal_id": CONFIG_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await client.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": CONFIG_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


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


@pytest.fixture
def boundary_timer_bpmn() -> str:
    """Non-interrupting Boundary-Timer (`cancelActivity="false"`, `PT0.002S`), aus dem
    offiziellen sartography/SpiffWorkflow-Repo (P6-S2, SLA-Zeitüberwachung)."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "boundary_timer_on_task.bpmn")
    with open(path, encoding="utf-8") as f:
        return f.read()
