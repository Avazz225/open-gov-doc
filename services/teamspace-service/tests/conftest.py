import os

import httpx
import pytest
from dms_db_base import build_engine, make_session_factory
from sqlalchemy import text
from teamspace_service.models import Base

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings (liest DMS_POSTGRES_DSN) wie für die
# Test-Fixtures oben - sonst testet TestClient(app) unbemerkt gegen die Live-DB,
# siehe PROGRESS.md "Tooling & Testing".
os.environ["DMS_POSTGRES_DSN"] = DSN

# `test_api.py` läuft wie jeder andere Service dieses Projekts gegen die echten,
# per docker-compose laufenden Nachbar-Services (kein Mocking) - `folder-service`
# für den Wurzelordner, `permission-service` für die Rollenzuweisung. Seit
# Post-Roadmap Phase 74 Session 3 (ADR 0160/ADR 0217) zusätzlich `auth-service`
# für die AD-Gruppen-Mitgliederabfrage.
FOLDER_SERVICE_URL = os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
AUTH_SERVICE_URL = os.environ.get("TEST_AUTH_SERVICE_URL", "http://localhost:8003")
os.environ["DMS_FOLDER_SERVICE_BASE_URL"] = FOLDER_SERVICE_URL
os.environ["DMS_PERMISSION_SERVICE_BASE_URL"] = PERMISSION_SERVICE_URL
os.environ["DMS_AUTH_SERVICE_BASE_URL"] = AUTH_SERVICE_URL
# `test_ad_group_invitation.py`'s own reconciliation-poll-tick test waits
# out a real tick rather than calling `_reconcile_ad_group_binding`
# directly (that call would run on a different event loop than
# `TestClient(app)`'s own AnyIO portal loop, see that test's docstring) -
# a short interval keeps the wait well under a second's worth of real
# time instead of the 300s production default.
os.environ["DMS_AD_GROUP_RECONCILIATION_POLL_INTERVAL_SECONDS"] = "1"

TEAMSPACE_SERVICE_PRINCIPAL_ID = "teamspace-service"


@pytest.fixture(scope="session", autouse=True)
async def _grant_service_group_lookup_permission():
    """Post-Roadmap Phase 74 Session 3 (ADR 0160/ADR 0217):
    `AuthServiceClient.get_group_members()` calls `GET /groups/{name}/
    members`, gated via `service.group_lookup`. Grants the SAME fixed
    principal id this service asserts in production (`clients.
    AuthServiceClient._PRINCIPAL_ID`), same pattern as
    `notification-service/tests/conftest.py`'s own
    `_grant_service_user_lookup_permission`."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "service-group-lookup")
        existing = (
            await pc.get(
                "/role-assignments", params={"principal_id": TEAMSPACE_SERVICE_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "service",
                "principal_id": TEAMSPACE_SERVICE_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS teamspace"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "TRUNCATE teamspace.teamspace_member, teamspace.teamspace_appointment, "
                "teamspace.teamspace_contact, teamspace.teamspace_ad_group_binding, "
                "teamspace.teamspace CASCADE"
            )
        )
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS teamspace"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
