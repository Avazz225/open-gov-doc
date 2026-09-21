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


MIGRATION_TESTS_PRINCIPAL_ID = "migration-tests"


@pytest.fixture(scope="session", autouse=True)
async def _grant_migration_admin_permission():
    """Phase 59 Session 5: `POST`/`DELETE /paired-installations` now
    require `admin.migration_management` - grants it to the same fixed
    test principal `test_api.py`'s `_DMS_PRINCIPAL_HEADERS` already sends
    on every request (kein Cross-File-Import von Test-Konstanten, gleiche
    Projektkonvention wie andernorts)."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-migration")
        existing = (
            await pc.get("/role-assignments", params={"principal_id": MIGRATION_TESTS_PRINCIPAL_ID})
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": MIGRATION_TESTS_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture(autouse=True)
async def _reset_approval_config():
    """Vier-Augen (4.3) für `migration.transfer.start` bleibt per Default aus -
    einzelne Tests aktivieren es gezielt, diese Fixture setzt danach zurück.
    `PUT /approval-config/{action_type}` ist seit P32-S1 (ADR 0130) selbst
    gegatet (`admin.user_management`) - reuses the already-running
    `migration-service` container's own bootstrapped principal (`main.py`'s
    `_ensure_config_admin_permission`, granted `domain-admin-users` at
    startup) instead of a separate test-only fixture."""
    yield
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as client:
        await client.put(
            "/approval-config/migration.transfer.start",
            json={"requires_approval": False},
            headers={"X-DMS-Principal": "migration-service"},
        )
