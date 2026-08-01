import os
import uuid

import httpx
import pytest
from dms_db_base import build_engine, make_session_factory
from sqlalchemy import text
from workflow_service.models import Base

DSN = os.environ.get(
    "TEST_POSTGRES_DSN",
    "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms",
)
# Erzwingt dieselbe DB für die App-Settings (liest DMS_POSTGRES_DSN) wie für die
# Test-Fixtures oben - sonst testet TestClient(app) unbemerkt gegen die Live-DB,
# siehe PROGRESS.md "Tooling & Testing".
os.environ["DMS_POSTGRES_DSN"] = DSN
NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")
os.environ["DMS_NATS_URL"] = NATS_URL

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
# Fester Principal statt uuid4() je Testlauf, damit die Rollenzuweisung
# idempotent bleibt (Unique-Constraint auf permission-service-Seite) - echter
# Aufruf gegen den laufenden permission-service (kein Mocking, P6-S6-Retrofit
# für Prozessdefinitionen/Script-Task-Upload, Capability `admin.object_config`).
CONFIG_ADMIN_PRINCIPAL_ID = "workflow-test-config-admin"


@pytest.fixture
def admin_headers() -> dict[str, str]:
    """Fixture statt Modul-Konstanten-Import (siehe PROGRESS.md "Tooling &
    Testing": `from conftest import x` funktioniert mit `--import-mode=importlib`
    nicht zuverlässig über Test-Module hinweg)."""
    return {"X-DMS-Principal": CONFIG_ADMIN_PRINCIPAL_ID}


@pytest.fixture(scope="session", autouse=True)
async def _grant_config_admin_permission():
    async with httpx.AsyncClient(base_url=PERMISSION_SERVICE_URL) as client:
        roles = (await client.get("/roles")).json()
        role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-config")
        existing = (
            await client.get(
                "/role-assignments", params={"principal_id": CONFIG_ADMIN_PRINCIPAL_ID}
            )
        ).json()
        if any(a["role_id"] == role_id for a in existing):
            return
        response = await client.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": CONFIG_ADMIN_PRINCIPAL_ID,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        response.raise_for_status()


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS workflow"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("TRUNCATE workflow.process_instance, workflow.process_definition CASCADE")
        )
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS workflow"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s


@pytest.fixture
def manual_task_bpmn() -> str:
    path = os.path.join(os.path.dirname(__file__), "fixtures", "script_and_manual.bpmn")
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def no_tasks_bpmn() -> str:
    path = os.path.join(os.path.dirname(__file__), "fixtures", "no_tasks.bpmn")
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def lanes_bpmn() -> str:
    path = os.path.join(os.path.dirname(__file__), "fixtures", "lanes.bpmn")
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def boundary_timer_bpmn() -> str:
    """Non-interrupting Boundary-Timer (`cancelActivity="false"`, `PT0.002S`), aus dem
    offiziellen sartography/SpiffWorkflow-Repo (P6-S2, SLA-Zeitüberwachung)."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "boundary_timer_on_task.bpmn")
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def signature_task_bpmn() -> str:
    """Ein einzelner Manual Task mit `camunda:properties`-Extension
    `taskType=signature`/`requiredLevel=aes` (3.10, P6-S7) - siehe
    spiff_adapter.py für die CamundaParser-Umstellung, die diese Extensions
    überhaupt sichtbar macht."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "signature_task.bpmn")
    with open(path, encoding="utf-8") as f:
        return f.read()


DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
SIGNATURE_SERVICE_URL = os.environ.get("TEST_SIGNATURE_SERVICE_URL", "http://localhost:8017")
AUTH_SERVICE_URL = os.environ.get("TEST_AUTH_SERVICE_URL", "http://localhost:8003")


def _read_sample_pdf() -> bytes:
    """Synchroner Helfer statt `open()` direkt in einer `async def`-Fixture
    (ruff ASYNC230) - reine lokale Dateilesung, keine echte Blockierungsgefahr
    in diesen Tests, aber der Linter unterscheidet nicht danach."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")
    with open(path, "rb") as f:
        return f.read()


@pytest.fixture
async def real_signer():
    """Echtes `auth-service`-Konto (kein Mocking) - Grundlage für eine echte,
    beim Signature Service erzeugte Signatur (siehe `real_signature`)."""
    username = f"wf-sig-test-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=AUTH_SERVICE_URL) as client:
        token = (
            await client.post("/login", json={"username": "users-admin", "password": "users-admin"})
        ).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        created = (
            await client.post(
                "/users",
                json={
                    "username": username,
                    "email": f"{username}@example.com",
                    "password": "testpass123",
                    "first_name": "Workflow",
                    "last_name": "Signer",
                },
                headers=headers,
            )
        ).json()
        yield username
        await client.delete(f"/users/{created['id']}", headers=headers)


@pytest.fixture
async def real_signature(real_signer):
    """Echtes, gegen document-service + signature-service erzeugtes signiertes
    PDF (kein Mocking von Sibling-Services) - liefert `(document_id,
    signature_id, level)`. Grundlage für den Signature-Task-Gating-Test in
    test_api.py."""
    pdf_bytes = _read_sample_pdf()

    async with httpx.AsyncClient(base_url=DOCUMENT_SERVICE_URL) as doc_client:
        document = (
            await doc_client.post(
                "/documents",
                data={"title": "Workflow-Signatur-Testdokument", "created_by": "alice"},
                files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
            )
        ).json()

    async with httpx.AsyncClient(base_url=SIGNATURE_SERVICE_URL) as sig_client:
        response = await sig_client.post(
            "/signatures",
            json={
                "document_id": document["id"],
                "level": "aes",
                "signer_principal_id": real_signer,
            },
        )
        response.raise_for_status()
        signature = response.json()

    yield document["id"], signature["id"], signature["level"]


@pytest.fixture
async def real_ses_signature(real_signer):
    """Wie `real_signature`, aber Niveau SES - Grundlage für den
    Mindestniveau-Ablehnungstest (`requiredLevel=aes` in
    `signature_task.bpmn`)."""
    pdf_bytes = _read_sample_pdf()

    async with httpx.AsyncClient(base_url=DOCUMENT_SERVICE_URL) as doc_client:
        document = (
            await doc_client.post(
                "/documents",
                data={"title": "Workflow-SES-Testdokument", "created_by": "alice"},
                files={"file": ("sample.pdf", pdf_bytes, "application/pdf")},
            )
        ).json()

    async with httpx.AsyncClient(base_url=SIGNATURE_SERVICE_URL) as sig_client:
        response = await sig_client.post(
            "/signatures",
            json={
                "document_id": document["id"],
                "level": "ses",
                "signer_principal_id": real_signer,
            },
        )
        response.raise_for_status()
        signature = response.json()

    yield document["id"], signature["id"], signature["level"]
