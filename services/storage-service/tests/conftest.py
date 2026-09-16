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

import httpx  # noqa: E402
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

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
# Post-Roadmap Phase 38 Session 3: `guard-config`/`guard-status/{id}/config`/
# `guard-status/{id}/reidentify`/`operational-config` now require
# `admin.storage` - most tests in this file exercise exactly these
# endpoints, so `client` (test_api.py) carries this principal as its
# default `X-DMS-Principal` header rather than every test passing it
# explicitly.
STORAGE_ADMIN_PRINCIPAL_ID = "storage-service-tests"


@pytest.fixture(scope="session", autouse=True)
async def _grant_storage_permission():
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-storage")
        existing = (
            await pc.get("/role-assignments", params={"principal_id": STORAGE_ADMIN_PRINCIPAL_ID})
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": STORAGE_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


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
        # Post-Roadmap Phase 22 Session 6/7 (ADR 0091/0092) - ohne diese
        # beiden Zeilen würden `test_repository.py`s direkte `session`-
        # Fixture-Tests (anders als `test_api.py`s `client`-Fixture, die über
        # Restore-Fixtures wie `operational_config_client` selbst aufräumt)
        # Zustand zwischen unabhängigen Testläufen leaken - exakt das Muster,
        # das in dieser Session bereits bei zwei anderen Services gefunden
        # wurde (`permission-service`/`signature-service`).
        await conn.execute(text("DELETE FROM storage.operational_config"))
        await conn.execute(text("DELETE FROM storage.target_override"))
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
