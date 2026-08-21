import os
import uuid
from io import BytesIO

import httpx
import pytest
from document_service.main import app
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas

# Deliberately self-contained (no cross-file import from test_api.py) - same
# project convention as test_export.py.
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
ROLE_ADMIN_PRINCIPAL_ID = "document-service-test-role-admin"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _real_pdf(text: str = "GEHEIM") -> bytes:
    """200x200pt, one page, `text` drawn near the bottom of the page -
    reportlab is bottom-left-origin, so y=10 lands near the BOTTOM of the
    page in every coordinate system (including the fraction-based one
    `apply_redactions` uses, see rendering-service's own
    `test_redaction.py` for the empirically-verified mapping)."""
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    c.drawString(10, 10, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def upload(client, *, content=None, title="Akte", created_by="alice", **extra):
    data = {"title": title, "created_by": created_by, **extra}
    files = {"file": ("akte.pdf", content or _real_pdf(), "application/pdf")}
    return client.post("/documents", data=data, files=files)


def _grant_read(principal_id: str, resource_id: str = "root") -> None:
    """Same pattern as test_export.py's `_grant_document_read`, duplicated
    rather than imported (see module docstring)."""
    role = httpx.post(
        f"{PERMISSION_SERVICE_URL}/roles",
        json={
            "name": f"redaction-test-role-{uuid.uuid4().hex[:8]}",
            "permissions": ["document.read"],
        },
        headers={"X-DMS-Principal": ROLE_ADMIN_PRINCIPAL_ID},
        timeout=30.0,
    )
    role.raise_for_status()
    assignment = httpx.post(
        f"{PERMISSION_SERVICE_URL}/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": principal_id,
            "role_id": role.json()["id"],
            "resource_id": resource_id,
        },
        timeout=30.0,
    )
    assignment.raise_for_status()
    assert assignment.json()["status"] == "created", (
        f"Rollenzuweisung wurde nicht sofort wirksam: {assignment.json()}"
    )


REGION_BOTTOM_STRIP = [{"page_number": 1, "x": 0.0, "y": 0.8, "width": 1.0, "height": 0.2}]


def test_redact_requires_principal_header(client):
    document_id = upload(client).json()["id"]
    response = client.post(
        f"/documents/{document_id}/redact",
        json={"regions": REGION_BOTTOM_STRIP, "created_by": "alice"},
    )
    assert response.status_code == 401


def test_redact_requires_read_permission(client):
    document_id = upload(client).json()["id"]
    response = client.post(
        f"/documents/{document_id}/redact",
        json={"regions": REGION_BOTTOM_STRIP, "created_by": "alice"},
        headers={"X-DMS-Principal": f"unpriv-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 403


def test_redact_404_for_unknown_document(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_read(principal)
    response = client.post(
        "/documents/does-not-exist/redact",
        json={"regions": REGION_BOTTOM_STRIP, "created_by": "alice"},
        headers={"X-DMS-Principal": principal},
    )
    assert response.status_code == 404


def test_redact_rejects_non_pdf_document(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_read(principal)
    # upload() defaults to a real PDF - force a non-PDF upload instead.
    document_id = client.post(
        "/documents",
        data={"title": "Notiz", "created_by": "alice"},
        files={"file": ("notiz.txt", b"Hallo", "text/plain")},
    ).json()["id"]

    response = client.post(
        f"/documents/{document_id}/redact",
        json={"regions": REGION_BOTTOM_STRIP, "created_by": "alice"},
        headers={"X-DMS-Principal": principal},
    )
    assert response.status_code == 422


def test_redact_rejects_empty_regions(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_read(principal)
    document_id = upload(client).json()["id"]

    response = client.post(
        f"/documents/{document_id}/redact",
        json={"regions": [], "created_by": "alice"},
        headers={"X-DMS-Principal": principal},
    )
    assert response.status_code == 422


def test_redact_creates_a_new_document_linked_to_the_original(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_read(principal)
    original = upload(client, title="Bescheid").json()

    response = client.post(
        f"/documents/{original['id']}/redact",
        json={"regions": REGION_BOTTOM_STRIP, "created_by": "bob"},
        headers={"X-DMS-Principal": principal},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] != original["id"]
    assert body["derived_from_document_id"] == original["id"]
    assert body["derived_from_version_number"] == original["current_version_number"]
    assert body["derivation_type"] == "redaction"
    assert body["created_by"] == "bob"
    assert "geschwärzt" in body["title"]


def test_redact_actually_removes_the_covered_text(client):
    """The core correctness property of this session (ADR 0115) - via the
    real API and the real rendering-service, not a unit-level shortcut."""
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_read(principal)
    original = upload(client, content=_real_pdf("STRENG GEHEIM")).json()

    redacted_id = client.post(
        f"/documents/{original['id']}/redact",
        json={"regions": REGION_BOTTOM_STRIP, "created_by": "alice"},
        headers={"X-DMS-Principal": principal},
    ).json()["id"]

    redacted_content = client.get(f"/documents/{redacted_id}/content").content
    reader = PdfReader(BytesIO(redacted_content))
    assert "STRENG GEHEIM" not in reader.pages[0].extract_text()

    original_content = client.get(f"/documents/{original['id']}/content").content
    assert "STRENG GEHEIM" in PdfReader(BytesIO(original_content)).pages[0].extract_text()


def test_redact_inherits_classification_level_from_the_original(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_read(principal)
    original = upload(client).json()
    client.put(
        f"/documents/{original['id']}/classification-level",
        json={"classification_level": "GEHEIM", "changed_by": "alice"},
        headers={"X-DMS-Principal": "document-service-test-classification-admin"},
    )

    redacted = client.post(
        f"/documents/{original['id']}/redact",
        json={"regions": REGION_BOTTOM_STRIP, "created_by": "alice"},
        headers={"X-DMS-Principal": principal},
    ).json()

    assert redacted["classification_level"] == "GEHEIM"


def test_list_derived_documents_shows_the_redacted_copy(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_read(principal)
    original = upload(client).json()

    redacted_id = client.post(
        f"/documents/{original['id']}/redact",
        json={"regions": REGION_BOTTOM_STRIP, "created_by": "alice"},
        headers={"X-DMS-Principal": principal},
    ).json()["id"]

    response = client.get(f"/documents/{original['id']}/derived")

    assert response.status_code == 200
    assert [d["id"] for d in response.json()] == [redacted_id]


def test_list_derived_documents_empty_for_a_document_with_no_copies(client):
    document_id = upload(client).json()["id"]
    response = client.get(f"/documents/{document_id}/derived")
    assert response.status_code == 200
    assert response.json() == []


def test_redaction_preview_page_count(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_read(principal)
    document_id = upload(client).json()["id"]

    response = client.get(
        f"/documents/{document_id}/redaction-preview/page-count",
        headers={"X-DMS-Principal": principal},
    )

    assert response.status_code == 200
    assert response.json() == {"page_count": 1}


def test_redaction_preview_page_count_requires_read_permission(client):
    document_id = upload(client).json()["id"]
    response = client.get(
        f"/documents/{document_id}/redaction-preview/page-count",
        headers={"X-DMS-Principal": f"unpriv-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 403


def test_redaction_preview_page_image(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_read(principal)
    document_id = upload(client).json()["id"]

    response = client.get(
        f"/documents/{document_id}/redaction-preview/page-image",
        params={"page_number": 1},
        headers={"X-DMS-Principal": principal},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")
