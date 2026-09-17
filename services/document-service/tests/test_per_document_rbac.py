"""Real per-document RBAC resource (Post-Roadmap Phase 39 Session 4, ADR
0154) - mirrors `case-service`'s identical ADR-0144 test pattern (isolate
one resource via `inherit=False`, prove a directly-assigned grant is the
only thing that restores access), applied to documents for the first
time."""

import httpx
import pytest
from document_service.main import app
from document_service.settings import Settings
from fastapi.testclient import TestClient

settings = Settings()
PERMISSION_SERVICE_URL = settings.permission_service_base_url
# Must match conftest.py's respective constants - no cross-file import of
# test constants, same convention as elsewhere in this project.
ROLE_ADMIN_HEADERS = {"X-DMS-Principal": "document-service-test-role-admin"}
DELETION_ADMIN_HEADERS = {"X-DMS-Principal": "document-service-test-deletion-admin"}


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "document-service-tests"}) as c:
        yield c


@pytest.fixture
def real_folder_id():
    with httpx.Client(
        base_url=settings.folder_service_base_url,
        timeout=10.0,
        headers={"X-DMS-Principal": "document-service-test-folder-setup"},
    ) as fc:
        response = fc.post("/folders", json={"name": "Testordner-PerDoc", "created_by": "alice"})
        response.raise_for_status()
        folder_id = response.json()["id"]
        yield folder_id
        fc.delete(f"/folders/{folder_id}")


def _upload(client, *, title: str) -> str:
    response = client.post(
        "/documents",
        data={"title": title, "created_by": "alice"},
        files={"file": (f"{title}.txt", b"Inhalt", "text/plain")},
    )
    response.raise_for_status()
    return response.json()["id"]


def test_document_gets_a_real_resource_node_on_creation(client):
    document_id = _upload(client, title="Ressourcenknoten-Test")

    resource = httpx.get(f"{PERMISSION_SERVICE_URL}/resources/{document_id}", timeout=10.0)
    assert resource.status_code == 200
    body = resource.json()
    assert body["resource_id"] == document_id
    assert body["parent_id"] == "root"
    assert body["resource_type"] == "document"


def test_isolating_a_document_removes_default_access_until_explicitly_granted(client):
    """The actual point of this session: a document's own `ResourceNode`
    lets a role be assigned scoped to just THAT document, not the whole
    installation - proven by isolating one document from the default
    "everyone" grant (`inherit=False`, same mechanism `case-service`/
    `search-service`'s own tests use), then showing that only a principal
    with a document-specific grant can still read it, while an ordinary
    document remains unaffected."""
    visible_id = _upload(client, title="Sichtbar")
    isolated_id = _upload(client, title="Isoliert")

    patch_response = httpx.patch(
        f"{PERMISSION_SERVICE_URL}/resources/{isolated_id}",
        json={"inherit": False},
        timeout=10.0,
    )
    patch_response.raise_for_status()

    scoped_principal = "document-service-tests-scoped-reader"
    denied = client.get(f"/documents/{isolated_id}", headers={"X-DMS-Principal": scoped_principal})
    assert denied.status_code == 403

    still_visible = client.get(
        f"/documents/{visible_id}", headers={"X-DMS-Principal": scoped_principal}
    )
    assert still_visible.status_code == 200

    created_role = httpx.post(
        f"{PERMISSION_SERVICE_URL}/roles",
        json={"name": f"document-reader-{isolated_id}", "permissions": ["document.read"]},
        headers=ROLE_ADMIN_HEADERS,
        timeout=10.0,
    ).json()
    assert created_role["status"] == "created"
    assignment_response = httpx.post(
        f"{PERMISSION_SERVICE_URL}/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": scoped_principal,
            "role_id": created_role["role"]["id"],
            "resource_id": isolated_id,
        },
        timeout=10.0,
    )
    assignment_response.raise_for_status()
    assert assignment_response.json()["status"] == "created"

    now_allowed = client.get(
        f"/documents/{isolated_id}", headers={"X-DMS-Principal": scoped_principal}
    )
    assert now_allowed.status_code == 200


def test_moving_a_document_reparents_its_resource_node(client, real_folder_id):
    document_id = _upload(client, title="Verschoben")

    original = httpx.get(f"{PERMISSION_SERVICE_URL}/resources/{document_id}", timeout=10.0).json()
    assert original["parent_id"] == "root"

    update_response = client.patch(f"/documents/{document_id}", json={"folder_id": real_folder_id})
    assert update_response.status_code == 200

    moved = httpx.get(f"{PERMISSION_SERVICE_URL}/resources/{document_id}", timeout=10.0).json()
    assert moved["parent_id"] == real_folder_id


def test_purging_a_document_removes_its_resource_node(client):
    document_id = _upload(client, title="Zu löschen")
    trash_response = client.post(f"/documents/{document_id}/trash", json={"deleted_by": "alice"})
    assert trash_response.status_code == 200

    purge_response = client.post(f"/documents/{document_id}/purge", headers=DELETION_ADMIN_HEADERS)
    assert purge_response.status_code == 204

    resource = httpx.get(f"{PERMISSION_SERVICE_URL}/resources/{document_id}", timeout=10.0)
    assert resource.status_code == 404
