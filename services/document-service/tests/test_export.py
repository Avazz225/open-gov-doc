import os
import uuid
from io import BytesIO

import httpx
import pytest
from document_service.audit_client import AuditServiceClient
from document_service.main import _run_folder_export_tick, app
from document_service.rendering_client import RenderingClient
from document_service.storage_client import StorageClient
from fastapi.testclient import TestClient
from pypdf import PdfReader
from reportlab.pdfgen import canvas

# Deliberately self-contained (no cross-file import from test_api.py) - same
# project convention as the existing test suites (see test_api.py's own
# comment on ROLE_ADMIN_PRINCIPAL_ID: "kein Cross-File-Import von
# Test-Konstanten").
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
STORAGE_SERVICE_URL = os.environ.get("TEST_STORAGE_SERVICE_URL", "http://localhost:8005")
RENDERING_SERVICE_URL = os.environ.get("TEST_RENDERING_SERVICE_URL", "http://localhost:8011")
AUDIT_SERVICE_URL = os.environ.get("TEST_AUDIT_SERVICE_URL", "http://localhost:8002")
ROLE_ADMIN_PRINCIPAL_ID = "document-service-test-role-admin"


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _real_pdf(text: str = "Hallo") -> bytes:
    buf = BytesIO()
    c = canvas.Canvas(buf, pagesize=(200, 200))
    c.drawString(10, 100, text)
    c.showPage()
    c.save()
    return buf.getvalue()


def upload(client, *, content=None, title="Vertrag", created_by="alice", **extra):
    data = {"title": title, "created_by": created_by, **extra}
    files = {"file": ("vertrag.pdf", content or _real_pdf(title), "application/pdf")}
    return client.post("/documents", data=data, files=files)


def _grant_read(principal_id: str, resource_id: str) -> None:
    """Post-Roadmap Phase 28 (ADR 0107) - grants `document.read` on
    `resource_id` for `principal_id`, same pattern as test_api.py's
    `_grant_root_permission`/`_grant_document_read`, duplicated rather than
    imported (see module docstring)."""
    role = httpx.post(
        f"{PERMISSION_SERVICE_URL}/roles",
        json={"name": f"export-test-role-{uuid.uuid4().hex[:8]}", "permissions": ["document.read"]},
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


def _grant_document_read(principal_id: str) -> None:
    _grant_read(principal_id, "root")


def _grant_folder_read(principal_id: str, folder_id: str) -> None:
    _grant_read(principal_id, folder_id)


def test_export_document_requires_read_permission(client):
    document_id = upload(client).json()["id"]

    response = client.post(
        f"/documents/{document_id}/export",
        headers={"X-DMS-Principal": f"principal-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 403


def test_export_document_returns_a_pdf_with_document_and_history_sections(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client).json()["id"]

    response = client.post(
        f"/documents/{document_id}/export", headers={"X-DMS-Principal": principal}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


def test_export_document_rejects_invalid_history_position(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client).json()["id"]

    response = client.post(
        f"/documents/{document_id}/export",
        params={"history_position": "sideways"},
        headers={"X-DMS-Principal": principal},
    )
    assert response.status_code == 422


def test_export_document_404_for_unknown_document(client):
    response = client.post(
        "/documents/unbekannt/export",
        headers={"X-DMS-Principal": f"principal-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 404


def test_get_and_update_export_config(client):
    response = client.get("/export-config")
    assert response.status_code == 200
    body = response.json()
    assert body["history_position"] == "after"
    # Output stamping defaults (Post-Roadmap Phase 31 Session 6, ADR 0117) -
    # disabled by default, so a fresh installation's export behavior is
    # unchanged from before this session.
    assert body["stamp_enabled"] is False
    assert body["stamp_type"] == "qr"
    assert body["stamp_value_template"] == "{kennzeichen}"
    assert body["stamp_position"] == "bottom-right"

    response = client.put(
        "/export-config",
        json={
            "history_position": "before",
            "stamp_enabled": True,
            "stamp_type": "barcode",
            "stamp_value_template": "{document_id}",
            "stamp_position": "top-left",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["history_position"] == "before"
    assert body["stamp_enabled"] is True
    assert body["stamp_type"] == "barcode"
    assert body["stamp_value_template"] == "{document_id}"
    assert body["stamp_position"] == "top-left"

    persisted = client.get("/export-config").json()
    assert persisted["history_position"] == "before"
    assert persisted["stamp_type"] == "barcode"


def test_update_export_config_rejects_diagonal_center_for_non_text_stamp(client):
    """Post-Roadmap Phase 31 Session 6 (ADR 0117): `diagonal-center` only
    makes sense for a rotated text stamp - a QR code/barcode drawn that way
    would be unreadable."""
    response = client.put(
        "/export-config",
        json={"stamp_type": "qr", "stamp_position": "diagonal-center"},
    )
    assert response.status_code == 422


def test_update_export_config_rejects_unknown_template_placeholder(client):
    response = client.put(
        "/export-config",
        json={"stamp_value_template": "{unbekannt}"},
    )
    assert response.status_code == 422


def test_update_export_config_rejects_invalid_stamp_type(client):
    response = client.put("/export-config", json={"stamp_type": "hologram"})
    assert response.status_code == 422


def _has_embedded_image(page) -> bool:
    resources = page.get("/Resources")
    if resources is None or "/XObject" not in resources:
        return False
    xobjects = resources["/XObject"]
    return any(xobjects[name]["/Subtype"] == "/Image" for name in xobjects)


def test_export_document_applies_configured_stamp_when_enabled(client):
    """Post-Roadmap Phase 31 Session 6 (ADR 0117): the export pipeline's new
    optional automatic stamping step, exercised end to end through the real
    running rendering-service (`POST /render/watermark`), not mocked."""
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client).json()["id"]

    config_response = client.put(
        "/export-config",
        json={
            "stamp_enabled": True,
            "stamp_type": "qr",
            "stamp_value_template": "{document_id}",
            "stamp_position": "bottom-right",
        },
    )
    assert config_response.status_code == 200

    response = client.post(
        f"/documents/{document_id}/export", headers={"X-DMS-Principal": principal}
    )
    assert response.status_code == 200
    reader = PdfReader(BytesIO(response.content))
    assert _has_embedded_image(reader.pages[0])


def test_export_document_no_stamp_when_disabled(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    _grant_document_read(principal)
    document_id = upload(client).json()["id"]

    response = client.post(
        f"/documents/{document_id}/export", headers={"X-DMS-Principal": principal}
    )
    assert response.status_code == 200
    reader = PdfReader(BytesIO(response.content))
    assert not _has_embedded_image(reader.pages[0])


def test_start_folder_export_requires_read_permission(client):
    response = client.post(
        "/folders/some-folder/export",
        headers={"X-DMS-Principal": f"principal-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 403


def test_start_folder_export_creates_a_pending_job(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    # Permission-service currently has no mechanism to register a new
    # resource node beyond "root" (POST /resources doesn't exist) - a
    # pre-existing, real limitation of this dev stack, not specific to
    # this feature (create_share_link's `document.folder_id or "root"`
    # has the identical constraint). "root" is therefore the only folder
    # ID a grant can realistically target here.
    folder_id = "root"
    _grant_folder_read(principal, folder_id)

    response = client.post(f"/folders/{folder_id}/export", headers={"X-DMS-Principal": principal})

    assert response.status_code == 202
    body = response.json()
    assert body["folder_id"] == folder_id
    assert body["status"] == "pending"
    assert body["history_position"] == "after"
    assert body["attempts"] == 0
    # Output stamping (Post-Roadmap Phase 31 Session 6, ADR 0117): frozen
    # onto the job from `ExportConfig` at creation time (default disabled).
    assert body["stamp_enabled"] is False
    assert body["stamp_type"] == "qr"


def test_get_folder_export_404_for_unknown_job(client):
    response = client.get("/folder-exports/unbekannt")
    assert response.status_code == 404


def test_folder_export_content_409_while_not_completed(client):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    # Permission-service currently has no mechanism to register a new
    # resource node beyond "root" (POST /resources doesn't exist) - a
    # pre-existing, real limitation of this dev stack, not specific to
    # this feature (create_share_link's `document.folder_id or "root"`
    # has the identical constraint). "root" is therefore the only folder
    # ID a grant can realistically target here.
    folder_id = "root"
    _grant_folder_read(principal, folder_id)
    job_id = client.post(
        f"/folders/{folder_id}/export", headers={"X-DMS-Principal": principal}
    ).json()["id"]

    response = client.get(f"/folder-exports/{job_id}/content")
    assert response.status_code == 409


async def test_folder_export_tick_processes_a_folder_and_completes(
    client, session_factory, monkeypatch
):
    """Post-Roadmap Phase 28 (ADR 0107) - end to end through the real
    poll-tick function (bypassing the poll loop's sleep interval, same
    pattern as rendering-service's `_run_retry_tick` tests), a real folder
    with two documents, real rendering-service/audit-service/storage-service
    calls."""
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    # Permission-service currently has no mechanism to register a new
    # resource node beyond "root" (POST /resources doesn't exist) - a
    # pre-existing, real limitation of this dev stack, not specific to
    # this feature (create_share_link's `document.folder_id or "root"`
    # has the identical constraint). "root" is therefore the only folder
    # ID a grant can realistically target here.
    folder_id = "root"
    _grant_folder_read(principal, folder_id)

    upload(client, title="A.pdf", folder_id=folder_id)
    upload(client, title="B.pdf", folder_id=folder_id)

    job_id = client.post(
        f"/folders/{folder_id}/export", headers={"X-DMS-Principal": principal}
    ).json()["id"]

    # `publish_event` (module-level, used for the final "document.exported"
    # events) still goes through `app.state.event_bus`, constructed inside
    # `TestClient`'s own portal loop - same real NATS-client-outliving-its-
    # loop hazard, but publish_event is called throughout main.py and isn't
    # worth dependency-injecting just for this test. Faked out instead, same
    # established pattern as the rest of this test suite
    # (`monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)`).
    async def fake_publish(subject: str, data: bytes) -> None:
        pass

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    # Fresh clients bound to THIS async test's own event loop - the ones on
    # `app.state` were constructed inside `TestClient`'s internal portal
    # loop and cannot be reused here (httpx's connection-pool internals are
    # bound to the loop that first touches them).
    storage = StorageClient(STORAGE_SERVICE_URL)
    rendering_client = RenderingClient(RENDERING_SERVICE_URL)
    audit_client = AuditServiceClient(AUDIT_SERVICE_URL)
    try:
        await _run_folder_export_tick(
            session_factory,
            storage=storage,
            rendering_client=rendering_client,
            audit_client=audit_client,
        )
    finally:
        await storage.close()
        await rendering_client.close()
        await audit_client.close()

    status_response = client.get(f"/folder-exports/{job_id}")
    assert status_response.json()["status"] == "completed"

    content_response = client.get(f"/folder-exports/{job_id}/content")
    assert content_response.status_code == 200
    assert content_response.headers["content-type"] == "application/pdf"
    assert content_response.content.startswith(b"%PDF")


async def test_folder_export_tick_applies_configured_stamp_per_document(
    client, session_factory, monkeypatch
):
    """Post-Roadmap Phase 31 Session 6 (ADR 0117): stamping is applied per
    document (Pass A) before the folder merge (Pass B), not once on the
    combined result - so every page keeps its own document's stamp even
    after being merged into one big folder export PDF."""
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    folder_id = "root"
    _grant_folder_read(principal, folder_id)

    upload(client, title="A.pdf", folder_id=folder_id)
    upload(client, title="B.pdf", folder_id=folder_id)

    config_response = client.put(
        "/export-config",
        json={
            "stamp_enabled": True,
            "stamp_type": "qr",
            "stamp_value_template": "{document_id}",
            "stamp_position": "top-right",
        },
    )
    assert config_response.status_code == 200

    job_id = client.post(
        f"/folders/{folder_id}/export", headers={"X-DMS-Principal": principal}
    ).json()["id"]

    async def fake_publish(subject: str, data: bytes) -> None:
        pass

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    storage = StorageClient(STORAGE_SERVICE_URL)
    rendering_client = RenderingClient(RENDERING_SERVICE_URL)
    audit_client = AuditServiceClient(AUDIT_SERVICE_URL)
    try:
        await _run_folder_export_tick(
            session_factory,
            storage=storage,
            rendering_client=rendering_client,
            audit_client=audit_client,
        )
    finally:
        await storage.close()
        await rendering_client.close()
        await audit_client.close()

    content_response = client.get(f"/folder-exports/{job_id}/content")
    assert content_response.status_code == 200
    reader = PdfReader(BytesIO(content_response.content))
    assert len(reader.pages) >= 3  # TOC + each document's own content+history pages
    # The TOC page (rendering_service's `_render_toc_pdf`, added fresh during
    # Pass B) has no source document of its own to stamp and is correctly
    # never stamped - unlike every carried-over Pass-A page (both documents'
    # content AND their own export-history section), which is.
    assert not _has_embedded_image(reader.pages[0])
    assert all(_has_embedded_image(page) for page in reader.pages[1:])


async def test_folder_export_tick_fails_gracefully_for_an_empty_folder(client, session_factory):
    principal = f"principal-{uuid.uuid4().hex[:8]}"
    # Permission-service currently has no mechanism to register a new
    # resource node beyond "root" (POST /resources doesn't exist) - a
    # pre-existing, real limitation of this dev stack, not specific to
    # this feature (create_share_link's `document.folder_id or "root"`
    # has the identical constraint). "root" is therefore the only folder
    # ID a grant can realistically target here.
    folder_id = "root"
    _grant_folder_read(principal, folder_id)

    job_id = client.post(
        f"/folders/{folder_id}/export", headers={"X-DMS-Principal": principal}
    ).json()["id"]

    # Fails before any client is actually used (empty folder), but the
    # signature still requires them - see the previous test for why fresh
    # instances instead of `app.state`'s.
    storage = StorageClient(STORAGE_SERVICE_URL)
    rendering_client = RenderingClient(RENDERING_SERVICE_URL)
    audit_client = AuditServiceClient(AUDIT_SERVICE_URL)
    try:
        await _run_folder_export_tick(
            session_factory,
            storage=storage,
            rendering_client=rendering_client,
            audit_client=audit_client,
        )
    finally:
        await storage.close()
        await rendering_client.close()
        await audit_client.close()

    body = client.get(f"/folder-exports/{job_id}").json()
    assert body["attempts"] == 1
    assert body["status"] == "pending"
    assert body["error_message"] is not None
