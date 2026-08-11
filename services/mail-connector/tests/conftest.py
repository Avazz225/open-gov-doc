import os

import httpx
import pytest
from dms_db_base import build_engine, make_session_factory
from mail_connector.models import Base
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
os.environ["DMS_POSTGRES_DSN"] = DSN
# "mailpit" (Docker-interner Hostname, Default in settings.py) löst vom
# Testlaufhost aus nicht auf - gleiche Überschreibung wie notification-
# service/tests/conftest.py.
os.environ["DMS_SMTP_HOST"] = os.environ.get("TEST_SMTP_HOST", "localhost")

WORKFLOW_SERVICE_URL = os.environ.get("TEST_WORKFLOW_SERVICE_URL", "http://localhost:8014")
CASE_SERVICE_URL = os.environ.get("TEST_CASE_SERVICE_URL", "http://localhost:8016")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")

# Gleiches "echter case-service/workflow-service statt Mocking"-Testmuster
# wie case-service/tests/conftest.py selbst (dessen Fixture-BPMN hierher
# kopiert, kein Cross-Service-Test-Import) - `resolve_match` braucht einen
# echten, über `POST /cases` angelegten Fall inkl. Vorgangsnummer.
CONFIG_ADMIN_PRINCIPAL_ID = "mail-connector-test-config-admin"


@pytest.fixture
def workflow_admin_headers() -> dict[str, str]:
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


@pytest.fixture
def real_case_id(workflow_admin_headers: dict[str, str]) -> str:
    path = os.path.join(os.path.dirname(__file__), "fixtures", "script_and_manual.bpmn")
    with open(path, "rb") as f:
        response = httpx.post(
            f"{WORKFLOW_SERVICE_URL}/process-definitions",
            data={"name": f"mail-connector-test-{os.urandom(4).hex()}"},
            files={"bpmn_xml": ("process.bpmn", f, "application/xml")},
            headers=workflow_admin_headers,
        )
    response.raise_for_status()
    process_definition_id = response.json()["id"]

    response = httpx.post(
        f"{CASE_SERVICE_URL}/cases",
        json={
            "name": "Testfall",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
    )
    response.raise_for_status()
    return response.json()["id"]


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS mail_connector"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE mail_connector.inbound_attachment CASCADE"))
        await conn.execute(text("TRUNCATE mail_connector.inbound_message CASCADE"))
        await conn.execute(text("TRUNCATE mail_connector.outbound_message CASCADE"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS mail_connector"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
