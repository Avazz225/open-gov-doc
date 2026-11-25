"""Real per-document RBAC resource (Post-Roadmap Phase 39 Session 4, ADR
0154) - mirrors `case-service`'s identical ADR-0144 test pattern (isolate
one resource via `inherit=False`, prove a directly-assigned grant is the
only thing that restores access), applied to documents for the first
time."""

import time

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
        response = fc.post(
            "/folders",
            json={"name": "Testordner-PerDoc", "created_by": "alice"},
            headers={"X-DMS-Principal": "alice"},
        )
        response.raise_for_status()
        folder_id = response.json()["id"]
        yield folder_id
        fc.delete(f"/folders/{folder_id}")


def _upload(client, *, title: str) -> str:
    response = client.post(
        "/documents",
        data={"title": title, "created_by": "alice"},
        files={"file": (f"{title}.txt", b"Inhalt", "text/plain")},
        headers={"X-DMS-Principal": "alice"},
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

    # The node is reparented via `document.resource.moved`, so asynchronously
    # - wait instead of racing the consumer, like the purge test below (failed
    # twice in P95-S1e/S2a while permission-service was working through
    # earlier events).
    for _ in range(50):
        moved = httpx.get(f"{PERMISSION_SERVICE_URL}/resources/{document_id}", timeout=10.0).json()
        if moved["parent_id"] == real_folder_id:
            break
        time.sleep(0.1)
    assert moved["parent_id"] == real_folder_id


def test_purging_a_document_removes_its_resource_node(client):
    document_id = _upload(client, title="Zu löschen")
    trash_response = client.post(
        f"/documents/{document_id}/trash",
        json={"deleted_by": "alice"},
        headers={"X-DMS-Principal": "alice"},
    )
    assert trash_response.status_code == 200

    purge_response = client.post(f"/documents/{document_id}/purge", headers=DELETION_ADMIN_HEADERS)
    assert purge_response.status_code == 204

    # The node is removed via `document.resource.deleted`, so asynchronously:
    # wait a little instead of racing the consumer (flaky before P91-S4).
    for _ in range(50):
        resource = httpx.get(f"{PERMISSION_SERVICE_URL}/resources/{document_id}", timeout=10.0)
        if resource.status_code == 404:
            break
        time.sleep(0.1)
    assert resource.status_code == 404


def test_folder_listing_leaves_out_documents_the_caller_cannot_read(client, real_folder_id):
    """P91-S4 (ADR 0259): a document isolated inside a readable folder no
    longer appears in that folder's listing for someone who can't open it;
    a service principal learns how many were left out."""

    def upload_into_folder(title: str) -> str:
        response = client.post(
            "/documents",
            data={"title": title, "folder_id": real_folder_id},
            files={"file": (f"{title}.txt", b"Inhalt", "text/plain")},
            headers={"X-DMS-Principal": "alice"},
        )
        response.raise_for_status()
        return response.json()["id"]

    visible_id = upload_into_folder("Sichtbar")
    isolated_id = upload_into_folder("Verschlossen")
    httpx.patch(
        f"{PERMISSION_SERVICE_URL}/resources/{isolated_id}", json={"inherit": False}, timeout=10.0
    ).raise_for_status()

    outsider = {"X-DMS-Principal": "document-service-tests-listing-outsider"}
    listing = client.get("/documents", params={"folder_id": real_folder_id}, headers=outsider)
    assert listing.status_code == 200
    assert [d["id"] for d in listing.json()] == [visible_id]
    assert "x-dms-hidden-count" not in listing.headers

    # P94-S3: a service reading as itself is trusted and sees both; acting
    # for the outsider it gets the outsider's view and the hidden count.
    as_service = client.get(
        "/documents",
        params={"folder_id": real_folder_id},
        headers={"X-DMS-Principal": "migration-service"},
    )
    assert sorted(d["id"] for d in as_service.json()) == sorted([visible_id, isolated_id])
    assert as_service.headers["x-dms-hidden-count"] == "0"
    for_outsider = client.get(
        "/documents",
        params={"folder_id": real_folder_id},
        headers={
            "X-DMS-Principal": "migration-service",
            "X-DMS-On-Behalf-Of": "document-service-tests-listing-outsider",
        },
    )
    assert for_outsider.headers["x-dms-hidden-count"] == "1"


def test_a_service_reading_as_itself_is_trusted_but_not_for_writes_or_on_behalf(client):
    """P94-S3 (user decision): an indexer must not be lockable out of a
    document - a narrowed resource (e.g. in a teamspace) had sent
    search-service into an endless redelivery loop. Reads as the service
    itself pass; reads for a user and every write are checked as before."""
    document_id = _upload(client, title="Indexer-Lesen")
    httpx.patch(
        f"{PERMISSION_SERVICE_URL}/resources/{document_id}", json={"inherit": False}, timeout=10.0
    ).raise_for_status()

    as_indexer = {"X-DMS-Principal": "search-service"}
    assert client.get(f"/documents/{document_id}", headers=as_indexer).status_code == 200
    assert client.get(f"/documents/{document_id}/content", headers=as_indexer).status_code == 200
    batch = client.post(
        "/documents/versions/current/batch",
        json={"document_ids": [document_id]},
        headers=as_indexer,
    )
    assert document_id in batch.json()["versions"]

    outsider = "document-service-tests-trust-outsider"
    assert (
        client.get(f"/documents/{document_id}", headers={"X-DMS-Principal": outsider}).status_code
        == 403
    )
    for_outsider = {"X-DMS-Principal": "search-service", "X-DMS-On-Behalf-Of": outsider}
    assert client.get(f"/documents/{document_id}", headers=for_outsider).status_code == 403
    write = client.patch(
        f"/documents/{document_id}", json={"title": "umbenannt"}, headers=as_indexer
    )
    assert write.status_code == 403
