import os

import pytest
from dms_db_base import build_engine, make_session_factory
from fleet_management_service.models import Base
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings (liest DMS_POSTGRES_DSN) wie für die
# Test-Fixtures oben - sonst testet TestClient(app) unbemerkt gegen die Live-DB,
# siehe PROGRESS.md "Tooling & Testing".
os.environ["DMS_POSTGRES_DSN"] = DSN


@pytest.fixture(autouse=True)
async def _clean_operators():
    """Operators (P84-S4) are created through the API by tests that only use
    the `client` fixture, so the `engine` fixture's cleanup below never ran
    for them - names then collided with leftovers on the next run. Cleared
    before every test instead. (`create_all` first: the very first test of a
    fresh test DB runs before the app's lifespan created the table.)"""
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS fleet"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DELETE FROM fleet.fleet_operator"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS fleet"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        # Reihenfolge respektiert FKs (P13-S2b: Installation-Runs/Rollouts/
        # Gruppen-Mitgliedschaften hängen an managed_installation).
        await conn.execute(text("DELETE FROM fleet.installation_run"))
        await conn.execute(text("DELETE FROM fleet.rollout"))
        await conn.execute(text("DELETE FROM fleet.update_plan"))
        await conn.execute(text("DELETE FROM fleet.installation_group_member"))
        await conn.execute(text("DELETE FROM fleet.installation_group"))
        await conn.execute(text("DELETE FROM fleet.managed_installation"))
        await conn.execute(text("DELETE FROM fleet.fleet_operator"))
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
