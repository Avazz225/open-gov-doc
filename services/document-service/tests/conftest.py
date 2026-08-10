import os

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
                "document.audit_trace_role_override CASCADE"
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
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s
