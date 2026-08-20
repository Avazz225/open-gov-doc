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
# Papierkorb-Familie (2.5, P15-S1): beide Rollen-Settings defaulten auf
# "dms-admin" (gleiches Muster wie kennzeichen_admin_role) - für Tests, die
# regulären und Verschlusssachen-Papierkorb tatsächlich unterscheiden müssen,
# vor dem `Settings()`-Import auf einen eigenen Rollennamen gesetzt, sonst
# wären beide Rollen in dieser Testumgebung ununterscheidbar identisch.
os.environ["DMS_CLASSIFIED_TRASH_HARD_DELETE_ADMIN_ROLE"] = "classified-trash-hard-delete-admin"
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
