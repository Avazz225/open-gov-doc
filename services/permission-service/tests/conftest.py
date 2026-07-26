import os

import pytest
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import NatsEventBusClient
from permission_service import repository
from permission_service.models import Base
from sqlalchemy import text

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")


@pytest.fixture(scope="session", autouse=True)
async def _ensure_folder_stream_exists():
    """Simuliert, dass der (noch nicht existierende) Folder Service schon
    einmal gelaufen ist und seinen Stream angelegt hat - ohne das würde
    `permission-service`s Subscribe beim App-Start mit SubjectNotFoundError
    fehlschlagen (siehe structure_consumer.start_consuming)."""
    producer = NatsEventBusClient(NATS_URL, stream="folder")
    await producer.connect()
    await producer.close()


@pytest.fixture(autouse=True)
async def _clean_tables():
    """Räumt VOR jedem Test auf, unabhängig von der genutzten Fixture-Kombination
    (analog zum audit-service-Muster) - nötig, da Tests teils über die App
    (eigene Engine-Verbindung) und teils über `session` direkt schreiben."""
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS permission"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "TRUNCATE permission.role_assignment, permission.effective_permission_cache, "
                "permission.role, permission.resource_node CASCADE"
            )
        )
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS permission"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        await repository.ensure_root_resource(s)
        await s.commit()
        yield s
