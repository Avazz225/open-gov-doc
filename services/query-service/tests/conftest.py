import os
import sys
from pathlib import Path

import pytest
from dms_db_base import build_engine, make_session_factory
from query_service.models import Base
from sqlalchemy import text

# `--import-mode=importlib` (workspace-weit, siehe pyproject.toml) verzichtet
# bewusst auf `tests/__init__.py`-Dateien - ein `tests.fixtures.x`-Dotted-
# Import waere daher nicht zuverlaessig aufloesbar. Stattdessen wird das
# `tests/fixtures`-Verzeichnis fuer die Dauer der Testsession direkt auf
# `sys.path` gelegt, damit `load_parser_plugin("fake_parser_plugin")`
# (flacher Modulname) genau den echten `importlib.import_module`-Pfad einer
# spaeteren echten Plugin-Installation nachbildet.
FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _fixtures_on_syspath():
    sys.path.insert(0, str(FIXTURES_DIR))
    yield
    sys.path.remove(str(FIXTURES_DIR))


DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB fuer die App-Settings (liest DMS_POSTGRES_DSN) wie fuer
# die Test-Fixtures unten - sonst testet TestClient(app) unbemerkt gegen die
# Live-DB, siehe PROGRESS.md "Tooling & Testing" (P5-S2-Datenverlust,
# P5b-S6-Leck). Erst seit P8-S2 relevant (P8-S1 hatte keine eigene DB).
os.environ["DMS_POSTGRES_DSN"] = DSN


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS query"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE query.manipulation_mode_status"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS query"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
