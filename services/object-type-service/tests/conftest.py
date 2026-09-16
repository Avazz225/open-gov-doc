import os

import httpx
import pytest
from dms_db_base import build_engine, make_session_factory
from object_type_service.models import Base
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings (liest DMS_POSTGRES_DSN) wie für die
# Test-Fixtures oben - sonst testet TestClient(app) unbemerkt gegen die Live-DB,
# siehe PROGRESS.md "Tooling & Testing" (P5-S2-Datenverlust, P5b-S6-Leck).
os.environ["DMS_POSTGRES_DSN"] = DSN

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
# Post-Roadmap Phase 38 Session 3: object type/layout/kennzeichen-config
# mutation now requires `admin.object_config` - most tests in this file
# exercise exactly these endpoints, so `client` (test_api.py) carries this
# principal as its default `X-DMS-Principal` header rather than every test
# passing it explicitly.
OBJECT_CONFIG_PRINCIPAL_ID = "object-type-service-tests"


@pytest.fixture(scope="session", autouse=True)
async def _grant_object_config_permission():
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-config")
        existing = (
            await pc.get("/role-assignments", params={"principal_id": OBJECT_CONFIG_PRINCIPAL_ID})
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": OBJECT_CONFIG_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS object_type"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE object_type.object_type CASCADE"))
        # `kennzeichen_config` (P5e-S3) hat keine FK auf `object_type` und wird
        # daher von der obigen CASCADE-Truncate nicht mit erfasst - eigene
        # Zeile nötig, sonst bleibt der Zustand zwischen Tests hängen.
        await conn.execute(text("TRUNCATE object_type.kennzeichen_config"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS object_type"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
