import os
import uuid

import httpx
import pytest
from dms_db_base import build_engine, make_session_factory
from signature_service.models import Base
from sqlalchemy import text

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

DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
OBJECT_TYPE_SERVICE_URL = os.environ.get("TEST_OBJECT_TYPE_SERVICE_URL", "http://localhost:8007")
AUTH_SERVICE_URL = os.environ.get("TEST_AUTH_SERVICE_URL", "http://localhost:8003")
os.environ["DMS_DOCUMENT_SERVICE_BASE_URL"] = DOCUMENT_SERVICE_URL
os.environ["DMS_OBJECT_TYPE_SERVICE_BASE_URL"] = OBJECT_TYPE_SERVICE_URL
os.environ["DMS_AUTH_SERVICE_BASE_URL"] = AUTH_SERVICE_URL

SAMPLE_PDF_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "sample.pdf")


@pytest.fixture(autouse=True)
async def _clean_tables():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS signature"))
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE signature.signature, signature.internal_ca CASCADE"))
    await eng.dispose()
    yield


@pytest.fixture
async def engine():
    eng = build_engine(DSN)
    async with eng.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS signature"))
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = make_session_factory(engine)
    async with factory() as s:
        yield s


@pytest.fixture
def sample_pdf_bytes() -> bytes:
    with open(SAMPLE_PDF_PATH, "rb") as f:
        return f.read()


@pytest.fixture
async def pdf_document(sample_pdf_bytes):
    """Echtes, gegen den laufenden document-service angelegtes PDF-Dokument
    (kein Mocking von Sibling-Services) - liefert `(document_id,
    version_number)`. Version 1 wird bei der Anlage automatisch erzeugt."""
    async with httpx.AsyncClient(base_url=DOCUMENT_SERVICE_URL) as client:
        response = await client.post(
            "/documents",
            data={"title": "Signatur-Testdokument", "created_by": "alice"},
            files={"file": ("sample.pdf", sample_pdf_bytes, "application/pdf")},
        )
        response.raise_for_status()
        document = response.json()
        yield document["id"], document["current_version_number"]


@pytest.fixture
async def non_pdf_document():
    """Echtes, gegen document-service angelegtes Nicht-PDF-Dokument - Grundlage
    für den `content_type != application/pdf`-Ablehnungstest."""
    async with httpx.AsyncClient(base_url=DOCUMENT_SERVICE_URL) as client:
        response = await client.post(
            "/documents",
            data={"title": "Textdokument", "created_by": "alice"},
            files={"file": ("test.txt", b"Kein PDF", "text/plain")},
        )
        response.raise_for_status()
        document = response.json()
        yield document["id"], document["current_version_number"]


@pytest.fixture
async def real_signer():
    """Echtes `auth-service`-Konto (kein Mocking, siehe P6-S6-Retrofit-Muster
    bei notification-service) - liefert `username`, per Rückgabe nutzbar als
    `signer_principal_id`. Anlage/Löschung über das technische
    `users-admin`-Konto (P6-S5)."""
    username = f"sig-test-{uuid.uuid4().hex[:8]}"
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
                    "first_name": "Sig",
                    "last_name": "Test",
                },
                headers=headers,
            )
        ).json()
        yield username
        await client.delete(f"/users/{created['id']}", headers=headers)


@pytest.fixture
async def aes_required_object_type():
    """Echter Objekttyp mit `required_signature_level="aes"` (3.10, P6-S7) -
    liefert die `object_type_id`, Teardown löscht ihn wieder."""
    async with httpx.AsyncClient(base_url=OBJECT_TYPE_SERVICE_URL) as client:
        response = await client.post(
            "/object-types",
            json={
                "name": f"sig-test-type-{uuid.uuid4().hex[:8]}",
                "applies_to": "document",
                "required_signature_level": "aes",
            },
        )
        response.raise_for_status()
        object_type = response.json()
        yield object_type["id"]
        await client.delete(f"/object-types/{object_type['id']}")


@pytest.fixture
async def pdf_document_with_required_level(sample_pdf_bytes, aes_required_object_type):
    async with httpx.AsyncClient(base_url=DOCUMENT_SERVICE_URL) as client:
        response = await client.post(
            "/documents",
            data={
                "title": "Signatur-Testdokument (AES verlangt)",
                "created_by": "alice",
                "object_type_id": str(aes_required_object_type),
            },
            files={"file": ("sample.pdf", sample_pdf_bytes, "application/pdf")},
        )
        response.raise_for_status()
        document = response.json()
        yield document["id"], document["current_version_number"]
