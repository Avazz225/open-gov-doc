import os

import pytest
from dms_db_base import build_engine, make_session_factory
from folder_service import repository
from folder_service.models import Base
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings (liest DMS_POSTGRES_DSN) wie für die
# Test-Fixtures oben - sonst testet TestClient(app) unbemerkt gegen die Live-DB,
# siehe PROGRESS.md "Tooling & Testing" (P5-S2-Datenverlust, P5b-S6-Leck).
os.environ["DMS_POSTGRES_DSN"] = DSN
NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")
OBJECT_TYPE_SERVICE_URL = os.environ.get("TEST_OBJECT_TYPE_SERVICE_URL", "http://localhost:8007")


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS folder"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "TRUNCATE folder.legal_hold, folder.deletion_register_entry, folder.folder, "
                "folder.retention_config, folder.trash_config, folder.folder_template CASCADE"
            )
        )
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS folder"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        await repository.ensure_root_folder(s)
        await s.commit()
        yield s
