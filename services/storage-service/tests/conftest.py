import os

# Muss vor dem ersten Import von storage_service.main gesetzt werden (Settings()
# wird dort beim Modul-Import einmalig instanziiert) - conftest.py lädt vor den
# Testmodulen im selben Verzeichnis, daher hier statt in einer Fixture. Seit
# P5b-S6 eine echte Ziel-Liste (siehe settings.py) statt des früheren
# DMS_LOCAL_STORAGE_BASE_PATH - ein einzelnes "local"-Ziel, identisch zum
# Pydantic-Feld-Default außer dem Pfad.
os.environ.setdefault(
    "DMS_TARGETS", '[{"id":"local","type":"local","base_path":"/tmp/dms-storage-pytest"}]'
)

import pytest  # noqa: E402
from dms_db_base import build_engine, make_session_factory  # noqa: E402
from sqlalchemy import text  # noqa: E402
from storage_service.models import Base  # noqa: E402

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings (liest DMS_POSTGRES_DSN) wie für die
# Test-Fixtures oben - sonst testet TestClient(app) unbemerkt gegen die Live-DB,
# siehe PROGRESS.md "Tooling & Testing" (P5-S2-Datenverlust, P5b-S6-Leck).
os.environ["DMS_POSTGRES_DSN"] = DSN


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS storage"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.execute(text("DELETE FROM storage.object_copy"))
        await conn.execute(text("DELETE FROM storage.object_metadata"))
        await conn.execute(text("DELETE FROM storage.backend_identity"))
        await conn.execute(text("DELETE FROM storage.guard_config"))
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
