import os

import httpx
import pytest
from dms_db_base import build_engine, make_session_factory
from document_service.license_client import LicenseLimitClient
from document_service.models import Base
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
STORAGE_SERVICE_URL = os.environ.get("TEST_STORAGE_SERVICE_URL", "http://localhost:8005")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
# Post-Roadmap Phase 19 Session 6 (ADR 0071): `POST /roles` verlangt seit
# dieser Session `admin.user_management` - Testhelfer, die eigene Test-
# Rollen anlegen (`test_api.py::_grant_root_permission`), brauchen dafür ein
# berechtigtes Testprincipal.
ROLE_ADMIN_PRINCIPAL_ID = "document-service-test-role-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_role_admin_permission():
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-users")
        existing = (
            await pc.get("/role-assignments", params={"principal_id": ROLE_ADMIN_PRINCIPAL_ID})
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": ROLE_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


LEGAL_HOLD_ADMIN_PRINCIPAL_ID = "document-service-test-legal-hold-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_legal_hold_permission():
    """Post-Roadmap Phase 19 Session 10 (ADR 0075): `POST /legal-holds`/
    `.../release` verlangen seither `admin.legal_hold`."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-legal-hold")
        existing = (
            await pc.get(
                "/role-assignments", params={"principal_id": LEGAL_HOLD_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": LEGAL_HOLD_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


CLASSIFICATION_ADMIN_PRINCIPAL_ID = "document-service-test-classification-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_classification_permission():
    """Post-Roadmap Phase 31 Session 3 (ADR 0114): `PUT
    .../classification-level` requires `admin.classification`."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-classification")
        existing = (
            await pc.get(
                "/role-assignments", params={"principal_id": CLASSIFICATION_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": CLASSIFICATION_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


CLASSIFIED_DELETION_ADMIN_PRINCIPAL_ID = "document-service-test-classified-deletion-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_classified_deletion_permission():
    """Post-Roadmap Phase 32 Session 4 (ADR 0133): `GET /documents/deleted?
    scope=admin_classified`/`POST /documents/{id}/purge` (classified branch)
    require `admin.deletion_classified`, replacing the previous
    `classified-trash-hard-delete-admin` `X-DMS-Roles` string check."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-deletion-vs")
        existing = (
            await pc.get(
                "/role-assignments", params={"principal_id": CLASSIFIED_DELETION_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": CLASSIFIED_DELETION_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


RECORDS_QUARANTINE_ADMIN_PRINCIPAL_ID = "document-service-test-records-quarantine-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_records_quarantine_permission():
    """Post-Roadmap Phase 31 Session 5 (ADR 0116): `POST /records-quarantine`/
    `.../release`/`GET /records-quarantine` require `admin.records_quarantine`."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-records-quarantine")
        existing = (
            await pc.get(
                "/role-assignments",
                params={"principal_id": RECORDS_QUARANTINE_ADMIN_PRINCIPAL_ID},
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": RECORDS_QUARANTINE_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


RETENTION_ADMIN_PRINCIPAL_ID = "document-service-test-retention-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_retention_permission():
    """Post-Roadmap Phase 38 Session 3: `PUT /documents/{id}/retention`/
    `PUT /retention-config`/`PUT /trash-config` require `admin.retention`."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-retention")
        existing = (
            await pc.get("/role-assignments", params={"principal_id": RETENTION_ADMIN_PRINCIPAL_ID})
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": RETENTION_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


DOCUMENT_CONFIG_ADMIN_PRINCIPAL_ID = "document-service-test-document-config-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_document_config_permission():
    """Post-Roadmap Phase 38 Session 3: `upload-config`/`export-config`/
    `audit-trace-config`/`audit-trace-role-overrides`/`share-link-config`
    require `admin.document_config`."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-document-config")
        existing = (
            await pc.get(
                "/role-assignments", params={"principal_id": DOCUMENT_CONFIG_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": DOCUMENT_CONFIG_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


OBJECT_CONFIG_ADMIN_PRINCIPAL_ID = "document-service-test-object-config-admin"


@pytest.fixture(scope="session", autouse=True)
async def _grant_object_config_permission_for_test_setup():
    """Post-Roadmap Phase 38 Session 3: `test_metadata_integration.py`
    creates/deletes real object types against the live `object-type-
    service` as test setup - that service's `POST`/`DELETE /object-types`
    now require `admin.object_config` too, a cross-service test dependency
    (not this service's own gate)."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-config")
        existing = (
            await pc.get(
                "/role-assignments", params={"principal_id": OBJECT_CONFIG_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": OBJECT_CONFIG_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


ARCHIVAL_SERVICE_PRINCIPAL_ID = "archival-service"


@pytest.fixture(scope="session", autouse=True)
async def _grant_disposal_callback_permission():
    """Post-Roadmap Phase 38 Session 2: `PUT /documents/{id}/archived`/
    `dehydrated`/`rehydrated` require `document.disposal_callback`. Grants
    the SAME fixed principal id archival-service asserts in production
    (`archival_service.clients.DocumentClient`), not a separate test-only
    principal - so these tests exercise the exact real caller identity."""
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as pc:
        roles = (await pc.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "archival-service-callback")
        existing = (
            await pc.get(
                "/role-assignments", params={"principal_id": ARCHIVAL_SERVICE_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": ARCHIVAL_SERVICE_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture(autouse=True)
def _default_no_license_limit_exceeded(monkeypatch):
    """Lizenz-Limit-Blockade (Konzept 9.3, P9-S2) greift real gegen den
    laufenden license-service - in dieser Testumgebung ist häufig gar keine
    oder eine abgelaufene Testlizenz installiert, was `POST /documents` ohne
    diesen Patch standardmäßig mit `403` brechen könnte. Einzelne Tests für
    die Blockade selbst überschreiben `is_exceeded` gezielt wieder (siehe
    test_license_limit.py)."""

    async def _never_exceeded(self, dimension: str) -> bool:
        return False

    monkeypatch.setattr(LicenseLimitClient, "is_exceeded", _never_exceeded)


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS document"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text(
                "TRUNCATE document.document_lock, document.document_version, "
                "document.legal_hold, document.deletion_register_entry, "
                "document.document, document.upload_config, document.retention_config, "
                "document.trash_config, document.audit_trace_config, "
                "document.audit_trace_role_override, document.export_config, "
                "document.folder_export_job CASCADE"
            )
        )
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS document"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def session_factory(engine):
    return make_session_factory(engine)


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
