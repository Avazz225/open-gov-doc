import os
import sys
from pathlib import Path

import pytest
from dms_db_base import build_engine, make_session_factory
from license_service.models import Base
from sqlalchemy import text

# `--import-mode=importlib` (workspace-weit) kennt keine `tests/__init__.py` -
# `tests/fixtures` wird auf `sys.path` gelegt. Modul-Ebene statt Fixture: Test-
# Module importieren `license_factory` bereits beim Sammeln (vor jedem
# Fixture-Lauf), gleiches Muster wie query-service's `fake_parser_plugin`,
# hier aber als direkter `from license_factory import ...`-Import statt
# dynamischem `importlib`-Laden.
FIXTURES_DIR = Path(__file__).parent / "fixtures"
if str(FIXTURES_DIR) not in sys.path:
    sys.path.insert(0, str(FIXTURES_DIR))


DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
os.environ["DMS_POSTGRES_DSN"] = DSN


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS license"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE license.installed_license"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS license"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
