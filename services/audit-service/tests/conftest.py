import os
import tempfile

import pytest
from audit_service.models import Base
from dms_db_base import build_engine, make_session_factory
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings (liest DMS_POSTGRES_DSN) wie für die
# Test-Fixtures oben - sonst testet TestClient(app) unbemerkt gegen die Live-DB,
# siehe PROGRESS.md "Tooling & Testing" (P5-S2-Datenverlust, P5b-S6-Leck).
os.environ["DMS_POSTGRES_DSN"] = DSN
# Löschregister-Ledger (10.4, P11-S4): Code-Default ist ein Container-Pfad
# (/deletion-ledger/...), auf dem Host beim Testlauf nicht anlegbar - eigenes
# Temp-Verzeichnis statt dessen.
os.environ["DMS_DELETION_LEDGER_PATH"] = os.path.join(
    tempfile.mkdtemp(prefix="dms-deletion-ledger-test-"), "deletion-register.jsonl"
)


@pytest.fixture(autouse=True)
async def _clean_audit_table():
    """Räumt VOR jedem Test auf, unabhängig davon, welche Fixtures er nutzt -
    nötig, weil test_consumer_integration.py über die App/`main.py` eigene
    Engine-Verbindungen aufbaut, nicht über die `engine`/`session`-Fixtures
    unten, und sonst Zeilen zwischen Testdateien durchsickern würden.
    """
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS audit"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("ALTER TABLE audit.audit_event ADD COLUMN IF NOT EXISTS actor VARCHAR(255)")
        )
        await conn.execute(text("DELETE FROM audit.audit_event"))
        # Cutover-Zeile ebenfalls zuruecksetzen (P7-S2) - sonst leckt der
        # Cutover-Wert eines Tests in den naechsten, dessen Erwartungen an
        # get_actor_field_cutover_id() dann falsch waeren.
        await conn.execute(text("DELETE FROM audit.audit_meta"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS audit"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("ALTER TABLE audit.audit_event ADD COLUMN IF NOT EXISTS actor VARCHAR(255)")
        )
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
