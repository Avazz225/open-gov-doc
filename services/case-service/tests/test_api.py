import os
import uuid

import httpx
import pytest
from case_service import repository
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


def test_create_case_starts_workflow_instance(client, process_definition_id, case_headers):
    response = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "open"
    assert body["process_instance_id"] is not None
    assert body["closed_at"] is None


def test_create_case_with_unknown_process_definition_returns_400(client, case_headers):
    response = client.post(
        "/cases",
        json={"name": "X", "process_definition_id": 999999, "created_by": "alice"},
        headers=case_headers,
    )
    assert response.status_code == 400


def test_create_case_requires_authentication(client):
    response = client.post(
        "/cases",
        json={"name": "X", "process_definition_id": 999999, "created_by": "alice"},
    )
    assert response.status_code == 401


def test_create_case_returns_403_without_case_write_permission(client, everyone_role_without):
    everyone_role_without("case.write")
    response = client.post(
        "/cases",
        json={"name": "X", "process_definition_id": 999999, "created_by": "alice"},
        headers={"X-DMS-Principal": "case-service-tests-no-write"},
    )
    assert response.status_code == 403


def test_get_and_list_cases(client, process_definition_id, case_headers):
    created = client.post(
        "/cases",
        json={
            "name": "Akte A",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    get_response = client.get(f"/cases/{created['id']}", headers=case_headers)
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Akte A"

    list_response = client.get("/cases", params={"status": "open"}, headers=case_headers)
    assert created["id"] in {c["id"] for c in list_response.json()}


def test_get_unknown_case_returns_404(client, case_headers):
    response = client.get("/cases/does-not-exist", headers=case_headers)
    assert response.status_code == 404


def test_list_cases_requires_authentication(client):
    response = client.get("/cases")
    assert response.status_code == 401


def test_list_cases_returns_403_without_case_read_permission(client, everyone_role_without):
    everyone_role_without("case.read")
    response = client.get("/cases", headers={"X-DMS-Principal": "case-service-tests-no-read"})
    assert response.status_code == 403


def test_add_and_list_case_documents_resolves_current_version(
    client, process_definition_id, document_id, case_headers
):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    add_response = client.post(
        f"/cases/{case['id']}/documents",
        json={"document_id": document_id, "added_by": "alice"},
        headers=case_headers,
    )
    assert add_response.status_code == 201
    assert add_response.json()["current_version_number"] == 1
    assert add_response.json()["document_deleted_at"] is None

    list_response = client.get(f"/cases/{case['id']}/documents", headers=case_headers)
    assert list_response.status_code == 200
    [reference] = list_response.json()
    assert reference["document_id"] == document_id
    assert reference["current_version_number"] == 1
    assert reference["snapshot_version_number"] is None


def test_add_document_with_unknown_document_id_returns_400(
    client, process_definition_id, case_headers
):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    response = client.post(
        f"/cases/{case['id']}/documents",
        json={"document_id": "does-not-exist", "added_by": "alice"},
        headers=case_headers,
    )
    assert response.status_code == 400


def test_add_document_to_unknown_case_returns_404(client, document_id, case_headers):
    response = client.post(
        "/cases/does-not-exist/documents",
        json={"document_id": document_id, "added_by": "alice"},
        headers=case_headers,
    )
    assert response.status_code == 404


def test_remove_document_reference_soft_deletes_and_stops_resolving_version(
    client, process_definition_id, document_id, case_headers
):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()
    client.post(
        f"/cases/{case['id']}/documents",
        json={"document_id": document_id, "added_by": "alice"},
        headers=case_headers,
    )

    remove_response = client.request(
        "DELETE",
        f"/cases/{case['id']}/documents/{document_id}",
        json={"removed_by": "bob"},
        headers=case_headers,
    )
    assert remove_response.status_code == 200
    assert remove_response.json()["removed_by"] == "bob"

    [reference] = client.get(f"/cases/{case['id']}/documents", headers=case_headers).json()
    assert reference["removed_at"] is not None
    assert reference["current_version_number"] is None


def test_remove_unknown_reference_returns_404(client, process_definition_id, case_headers):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()
    response = client.request(
        "DELETE",
        f"/cases/{case['id']}/documents/does-not-exist",
        json={"removed_by": "bob"},
        headers=case_headers,
    )
    assert response.status_code == 404


def test_list_cases_due_for_archival_empty(client):
    """Bewusst UNGEGATET (Post-Roadmap Phase 19 Session 5, ADR 0070) - reiner
    Maschine-zu-Maschine-Rückruf von `archival-service`, kein
    `X-DMS-Principal` nötig."""
    response = client.get("/cases/due-for-archival")
    assert response.status_code == 200
    assert response.json() == []


def test_create_case_assigns_unique_vorgangsnummer(client, process_definition_id, case_headers):
    first = client.post(
        "/cases",
        json={"name": "A", "process_definition_id": process_definition_id, "created_by": "alice"},
        headers=case_headers,
    ).json()
    second = client.post(
        "/cases",
        json={"name": "B", "process_definition_id": process_definition_id, "created_by": "alice"},
        headers=case_headers,
    ).json()

    assert first["vorgangsnummer"] is not None
    assert second["vorgangsnummer"] is not None
    assert first["vorgangsnummer"] != second["vorgangsnummer"]


# --- Draft / pre-registration lifecycle (post-roadmap phase 31 session 2,
# ADR 0113) -----------------------------------------------------------


def test_create_case_as_draft_has_no_vorgangsnummer_or_registered_at(
    client, process_definition_id, case_headers
):
    response = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
            "draft": True,
        },
        headers=case_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["vorgangsnummer"] is None
    assert body["registered_at"] is None


def test_create_case_without_draft_is_registered_immediately(
    client, process_definition_id, case_headers
):
    response = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    )
    assert response.status_code == 201
    assert response.json()["registered_at"] is not None


def test_register_draft_case_assigns_vorgangsnummer(client, process_definition_id, case_headers):
    case_id = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
            "draft": True,
        },
        headers=case_headers,
    ).json()["id"]

    response = client.post(
        f"/cases/{case_id}/register", json={"registered_by": "alice"}, headers=case_headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["vorgangsnummer"] is not None
    assert body["registered_at"] is not None


def test_register_already_registered_case_returns_409(client, process_definition_id, case_headers):
    case_id = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()["id"]

    response = client.post(
        f"/cases/{case_id}/register", json={"registered_by": "alice"}, headers=case_headers
    )

    assert response.status_code == 409


def test_register_unknown_case_returns_404(client, case_headers):
    response = client.post(
        "/cases/does-not-exist/register", json={"registered_by": "alice"}, headers=case_headers
    )
    assert response.status_code == 404


def test_register_case_requires_case_write_permission(
    client, process_definition_id, case_headers, everyone_role_without
):
    case_id = client.post(
        "/cases",
        json={
            "name": "Bauantrag Mustermann",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
            "draft": True,
        },
        headers=case_headers,
    ).json()["id"]

    everyone_role_without("case.write")
    response = client.post(
        f"/cases/{case_id}/register",
        json={"registered_by": "alice"},
        headers={"X-DMS-Principal": "case-service-tests-no-write"},
    )
    assert response.status_code == 403


def test_lookup_by_vorgangsnummer_finds_matching_case(client, process_definition_id, case_headers):
    created = client.post(
        "/cases",
        json={"name": "A", "process_definition_id": process_definition_id, "created_by": "alice"},
        headers=case_headers,
    ).json()

    response = client.get(
        "/cases/by-vorgangsnummer",
        params={"value": created["vorgangsnummer"]},
        headers=case_headers,
    )

    assert response.status_code == 200
    assert [c["id"] for c in response.json()] == [created["id"]]


def test_lookup_by_vorgangsnummer_returns_empty_list_when_unknown(client, case_headers):
    response = client.get(
        "/cases/by-vorgangsnummer", params={"value": "does-not-exist"}, headers=case_headers
    )
    assert response.status_code == 200
    assert response.json() == []


def test_case_number_config_get_put_roundtrip(client, case_headers):
    default = client.get("/case-number-config", headers=case_headers)
    assert default.status_code == 200
    assert default.json()["format"] == "{YYYY}-{Laufende_Nummer}"

    updated = client.put(
        "/case-number-config", json={"format": "{YY}/{Laufende_Nummer}"}, headers=case_headers
    )
    assert updated.status_code == 200
    assert updated.json()["format"] == "{YY}/{Laufende_Nummer}"


def test_case_number_config_rejects_format_without_laufende_nummer(client, case_headers):
    response = client.put("/case-number-config", json={"format": "{YYYY}"}, headers=case_headers)
    assert response.status_code == 400


def test_case_number_config_rejects_unknown_placeholder(client, case_headers):
    response = client.put(
        "/case-number-config",
        json={"format": "{Unbekannt}-{Laufende_Nummer}"},
        headers=case_headers,
    )
    assert response.status_code == 400


def test_case_archival_config_get_put_roundtrip(client, case_headers):
    default = client.get("/case-archival-config", headers=case_headers)
    assert default.status_code == 200
    assert default.json()["default_archive_after_days_closed"] is None
    assert default.json()["archive_encryption_enabled"] is False

    updated = client.put(
        "/case-archival-config",
        json={"default_archive_after_days_closed": 90, "archive_encryption_enabled": True},
        headers=case_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["default_archive_after_days_closed"] == 90
    assert updated.json()["archive_encryption_enabled"] is True


def test_request_archive_returns_409_for_open_case(client, process_definition_id, case_headers):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    response = client.post(f"/cases/{case['id']}/archive-request", headers=case_headers)

    assert response.status_code == 409


def test_archive_request_requires_authentication(client, process_definition_id, case_headers):
    case = client.post(
        "/cases",
        json={
            "name": "Akte",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
        },
        headers=case_headers,
    ).json()

    response = client.post(f"/cases/{case['id']}/archive-request")

    assert response.status_code == 401


def test_get_case_archive_status_returns_404_for_unknown_case(client, case_headers):
    response = client.get("/cases/does-not-exist/archive-status", headers=case_headers)
    assert response.status_code == 404


async def test_archive_request_and_mark_archived_roundtrip_for_closed_case(
    client, session, case_headers
):
    case = await repository.create_case(
        session,
        case_id=f"case-{uuid.uuid4()}",
        name="Geschlossene Akte",
        object_type_id=None,
        attributes={},
        process_definition_id=1,
        process_instance_id=None,
        created_by="alice",
    )
    await repository.close_case(session, case, snapshots={})
    await session.commit()

    request_response = client.post(f"/cases/{case.id}/archive-request", headers=case_headers)
    assert request_response.status_code == 200
    assert request_response.json()["archive_after"] is not None

    status_response = client.get(f"/cases/{case.id}/archive-status", headers=case_headers)
    assert status_response.status_code == 200
    assert status_response.json()["archived_at"] is None

    # Bewusst ohne Header - GET /cases/due-for-archival und
    # PUT /cases/{id}/archived sind ungegatete Maschine-zu-Maschine-Rückrufe
    # von archival-service (siehe main.py-Docstrings, ADR 0070).
    due = client.get("/cases/due-for-archival").json()
    assert case.id in [c["id"] for c in due]

    archived_response = client.put(f"/cases/{case.id}/archived")
    assert archived_response.status_code == 200
    assert archived_response.json()["archived_at"] is not None

    due_after = client.get("/cases/due-for-archival").json()
    assert case.id not in [c["id"] for c in due_after]
