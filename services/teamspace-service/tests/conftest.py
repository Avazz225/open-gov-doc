import os

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
# für den Wurzelordner, `permission-service` für die Rollenzuweisung.
FOLDER_SERVICE_URL = os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
os.environ["DMS_FOLDER_SERVICE_BASE_URL"] = FOLDER_SERVICE_URL
os.environ["DMS_PERMISSION_SERVICE_BASE_URL"] = PERMISSION_SERVICE_URL


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS teamspace"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "TRUNCATE teamspace.teamspace_member, teamspace.teamspace_appointment, "
                "teamspace.teamspace_contact, teamspace.teamspace CASCADE"
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
