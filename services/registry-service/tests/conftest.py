import os

import httpx
import pytest
from dms_db_base import build_engine, make_session_factory
from registry_service.models import Base
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings (liest DMS_POSTGRES_DSN) wie für die
# Test-Fixtures oben - sonst testet TestClient(app) unbemerkt gegen die Live-DB,
# siehe PROGRESS.md "Tooling & Testing" (P5-S2-Datenverlust, P5b-S6-Leck).
os.environ["DMS_POSTGRES_DSN"] = DSN

# Branding config (7.3/8, P69-S2, ADR 0201) - echt gegen den laufenden
# permission-service (kein Mocking), gleiches Muster wie workflow-service's
# `_grant_config_admin_permission` für dieselbe Capability `admin.object_config`.
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
CONFIG_ADMIN_PRINCIPAL_ID = "registry-test-config-admin"


@pytest.fixture
def config_admin_headers() -> dict[str, str]:
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
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS registry"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.execute(text("DELETE FROM registry.service_instance"))
        await conn.execute(text("DELETE FROM registry.branding_config"))
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
