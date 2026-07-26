import os

# Muss vor dem ersten Import von storage_service.main gesetzt werden (Settings()
# wird dort beim Modul-Import einmalig instanziiert) - conftest.py lädt vor den
# Testmodulen im selben Verzeichnis, daher hier statt in einer Fixture.
os.environ.setdefault("DMS_LOCAL_STORAGE_BASE_PATH", "/tmp/dms-storage-pytest")

import pytest  # noqa: E402
from dms_db_base import build_engine, make_session_factory  # noqa: E402
from sqlalchemy import text  # noqa: E402
from storage_service.models import Base  # noqa: E402

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS storage"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    async with eng.begin() as conn:
        await conn.execute(text("DELETE FROM storage.object_metadata"))
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
