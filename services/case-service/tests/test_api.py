import os
import uuid

import httpx
import pytest
from case_service.main import app
from fastapi.testclient import TestClient

WORKFLOW_SERVICE_URL = os.environ.get("TEST_WORKFLOW_SERVICE_URL", "http://localhost:8014")
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def process_definition_id(workflow_admin_headers: dict[str, str]) -> int:
    """Real gegen den lokal laufenden workflow-service angelegt (P6-S1) -
    gleiches "kein Mocking von Sibling-Services"-Muster wie document-services
    folder_client/object_type_client-Integrationstests. Seit P6-S6 verlangt
    dieser Endpunkt die Capability `admin.object_config`, siehe conftest.py."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "script_and_manual.bpmn")
    with open(path, "rb") as f:
        response = httpx.post(
            f"{WORKFLOW_SERVICE_URL}/process-definitions",
            data={"name": f"case-service-test-{uuid.uuid4()}"},
            files={"bpmn_xml": ("process.bpmn", f, "application/xml")},
            headers=workflow_admin_headers,
        )
    response.raise_for_status()
    return response.json()["id"]


@pytest.fixture
def document_id() -> str:
    response = httpx.post(
        f"{DOCUMENT_SERVICE_URL}/documents",
        data={"title": "Testdokument", "created_by": "alice"},
        files={"file": ("test.txt", b"Inhalt", "text/plain")},
    )
    response.raise_for_status()
    return response.json()["id"]


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "case-service"


def test_create_case_starts_workflow_instance(client, process_definition_id):
    response = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "open"
    assert body["process_instance_id"] is not None
    assert body["closed_at"] is None


def test_create_case_with_unknown_process_definition_returns_400(client):
    response = client.post(
        "/cases",
        json={"name": "X", "process_definition_id": 999999, "created_by": "alice"},
    )
    assert response.status_code == 400


def test_get_and_list_cases(client, process_definition_id):
    created = client.post(
        "/cases",
        json={
            "name": "Akte A",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
    ).json()

    get_response = client.get(f"/cases/{created['id']}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Akte A"

    list_response = client.get("/cases", params={"status": "open"})
    assert created["id"] in {c["id"] for c in list_response.json()}


def test_get_unknown_case_returns_404(client):
    response = client.get("/cases/does-not-exist")
    assert response.status_code == 404


def test_add_and_list_case_documents_resolves_current_version(
    client, process_definition_id, document_id
):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
    ).json()

    add_response = client.post(
        f"/cases/{case['id']}/documents",
        json={"document_id": document_id, "added_by": "alice"},
    )
    assert add_response.status_code == 201
    assert add_response.json()["current_version_number"] == 1
    assert add_response.json()["document_deleted_at"] is None

    list_response = client.get(f"/cases/{case['id']}/documents")
    assert list_response.status_code == 200
    [reference] = list_response.json()
    assert reference["document_id"] == document_id
    assert reference["current_version_number"] == 1
    assert reference["snapshot_version_number"] is None


def test_add_document_with_unknown_document_id_returns_400(client, process_definition_id):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
    ).json()

    response = client.post(
        f"/cases/{case['id']}/documents",
        json={"document_id": "does-not-exist", "added_by": "alice"},
    )
    assert response.status_code == 400


def test_add_document_to_unknown_case_returns_404(client, document_id):
    response = client.post(
        "/cases/does-not-exist/documents",
        json={"document_id": document_id, "added_by": "alice"},
    )
    assert response.status_code == 404


def test_remove_document_reference_soft_deletes_and_stops_resolving_version(
    client, process_definition_id, document_id
):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
    ).json()
    client.post(
        f"/cases/{case['id']}/documents", json={"document_id": document_id, "added_by": "alice"}
    )

    remove_response = client.request(
        "DELETE",
        f"/cases/{case['id']}/documents/{document_id}",
        json={"removed_by": "bob"},
    )
    assert remove_response.status_code == 200
    assert remove_response.json()["removed_by"] == "bob"

    [reference] = client.get(f"/cases/{case['id']}/documents").json()
    assert reference["removed_at"] is not None
    assert reference["current_version_number"] is None


def test_remove_unknown_reference_returns_404(client, process_definition_id):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
    ).json()
    response = client.request(
        "DELETE",
        f"/cases/{case['id']}/documents/does-not-exist",
        json={"removed_by": "bob"},
    )
    assert response.status_code == 404
