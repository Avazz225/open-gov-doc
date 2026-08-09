import os

import httpx
import pytest
from dms_db_base import build_engine
from migration_service.models import Base
from sqlalchemy import text

# Tests laufen gegen den echten, per docker-compose laufenden Container (wie
# `webdav-connector`) statt gegen ein In-Prozess-`TestClient(app)`: der
# Selbst-Loopback-Smoke-Test (siehe `test_api.py`) braucht einen echten, von
# aussen erreichbaren HTTP-Server für die ausgehenden `PeerClient`-Aufrufe der
# Quellrolle gegen die eigene Zielrolle - ein reines In-Prozess-TestClient hat
# keinen von einem anderen Client aus erreichbaren Netzwerk-Listener.
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
DSN = os.environ.get(
    "TEST_POSTGRES_DSN", "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"
)


@pytest.fixture(autouse=True)
async def _clean_tables():
    """Direkter DB-Zugriff nur zum Aufräumen zwischen Tests - der eigentliche
    Service läuft als separater Container-Prozess (s. o.), nicht in diesem
    Testprozess."""
    engine = build_engine(DSN)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS migration"))
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.execute(text("DELETE FROM migration.inbound_transfer"))
        await conn.execute(text("DELETE FROM migration.transfer"))
        await conn.execute(text("DELETE FROM migration.paired_installation"))
    await engine.dispose()


@pytest.fixture(autouse=True)
async def _reset_approval_config():
    """Vier-Augen (4.3) für `migration.transfer.start` bleibt per Default aus -
    einzelne Tests aktivieren es gezielt, diese Fixture setzt danach zurück."""
    yield
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as client:
        await client.put(
            "/approval-config/migration.transfer.start", json={"requires_approval": False}
        )
