import os
import uuid

import httpx
import pytest
from case_service.main import app
from fastapi.testclient import TestClient

# Deliberately self-contained (no cross-file import from test_api.py's own
# local fixtures) - same project convention document-service's/folder-
# service's own test_attribute_pseudonymization.py already established.
WORKFLOW_SERVICE_URL = os.environ.get("TEST_WORKFLOW_SERVICE_URL", "http://localhost:8014")
OBJECT_TYPE_SERVICE_URL = os.environ.get("TEST_OBJECT_TYPE_SERVICE_URL", "http://localhost:8007")
CONFIG_ADMIN_HEADERS = {"X-DMS-Principal": "case-service-test-config-admin"}
CASE_HEADERS = {"X-DMS-Principal": "case-service-tests"}
PSEUDONYMIZATION_ADMIN_HEADERS = {"X-DMS-Principal": "case-service-test-pseudonymization-admin"}
REVEAL_ADMIN_HEADERS = {"X-DMS-Principal": "case-service-test-pii-reveal-admin"}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def process_definition_id():
    """Real BPMN registered against the real, running workflow-service -
    same fixture/file as `test_api.py`'s own `process_definition_id`,
    duplicated per the established self-contained-test-file convention."""
    path = os.path.join(os.path.dirname(__file__), "fixtures", "script_and_manual.bpmn")
    with open(path, "rb") as f:
        response = httpx.post(
            f"{WORKFLOW_SERVICE_URL}/process-definitions",
            data={"name": f"case-service-pseudonym-test-{uuid.uuid4()}"},
            files={"bpmn_xml": ("process.bpmn", f, "application/xml")},
            headers=CONFIG_ADMIN_HEADERS,
        )
    response.raise_for_status()
    return response.json()["id"]


@pytest.fixture
def object_type_id():
    """One attribute marked `personal_data: true` (SVNR), one not
    (Bezeichnung) - `applies_to="document"` since case-service has no own
    `applies_to` category (see `case_service.models.CaseArchivalConfig`'s
    docstring)."""
    with httpx.Client(
        base_url=OBJECT_TYPE_SERVICE_URL, timeout=10.0, headers=CONFIG_ADMIN_HEADERS
    ) as oc:
        response = oc.post(
            "/object-types",
            json={
                "name": f"pseudonym-case-test-type-{uuid.uuid4().hex[:8]}",
                "applies_to": "document",
                "attributes": [
                    {"name": "SVNR", "type": "string", "personal_data": True},
                    {"name": "Bezeichnung", "type": "string"},
                ],
            },
        )
        response.raise_for_status()
        type_id = response.json()["id"]
        yield type_id
        oc.delete(f"/object-types/{type_id}")


@pytest.fixture
def case_id(client, process_definition_id, object_type_id):
    response = client.post(
        "/cases",
        json={
            "name": "Testfall",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": {"SVNR": "123-45-6789", "Bezeichnung": "Testfall"},
        },
        headers=CASE_HEADERS,
    )
    response.raise_for_status()
    return response.json()["id"]


def _pseudonymize(client, case_id, attribute_name="SVNR", **overrides):
    payload = {"pseudonymized_by": "carol", **overrides}
    return client.post(
        f"/cases/{case_id}/attributes/{attribute_name}/pseudonymize",
        json=payload,
        headers=PSEUDONYMIZATION_ADMIN_HEADERS,
    )


def test_pseudonymize_requires_principal_header(client, case_id):
    response = client.post(
        f"/cases/{case_id}/attributes/SVNR/pseudonymize",
        json={"pseudonymized_by": "carol"},
        headers={"X-DMS-Principal": ""},
    )
    assert response.status_code == 401


def test_pseudonymize_requires_pseudonymization_permission(client, case_id):
    response = client.post(
        f"/cases/{case_id}/attributes/SVNR/pseudonymize",
        json={"pseudonymized_by": "carol"},
        headers={"X-DMS-Principal": f"unpriv-{uuid.uuid4().hex[:8]}"},
    )
    assert response.status_code == 403


def test_pseudonymize_404_for_unknown_case(client):
    response = _pseudonymize(client, "does-not-exist")
    assert response.status_code == 404


def test_pseudonymize_rejects_attribute_not_marked_personal_data(client, case_id):
    response = _pseudonymize(client, case_id, attribute_name="Bezeichnung")
    assert response.status_code == 400
    assert "personal_data" in response.json()["detail"]


def test_pseudonymize_rejects_unknown_attribute(client, case_id):
    response = _pseudonymize(client, case_id, attribute_name="does-not-exist")
    assert response.status_code == 400


def test_pseudonymize_rejects_attribute_with_no_value(
    client, process_definition_id, object_type_id
):
    case_id = client.post(
        "/cases",
        json={
            "name": "Ohne SVNR",
            "process_definition_id": process_definition_id,
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": {"Bezeichnung": "Testfall"},
        },
        headers=CASE_HEADERS,
    ).json()["id"]
    response = _pseudonymize(client, case_id)
    assert response.status_code == 400


def test_pseudonymize_succeeds_and_overwrites_the_live_value(client, case_id):
    response = _pseudonymize(client, case_id, reason="DSGVO-Löschanfrage")
    assert response.status_code == 201
    body = response.json()
    assert body["case_id"] == case_id
    assert body["attribute_name"] == "SVNR"
    assert body["pseudonymized_by"] == "carol"
    assert body["reason"] == "DSGVO-Löschanfrage"
    assert body["last_revealed_at"] is None
    assert "encrypted_value" not in body

    case = client.get(f"/cases/{case_id}", headers=CASE_HEADERS).json()
    assert case["attributes"]["SVNR"] == "[PSEUDONYMISIERT]"
    assert case["attributes"]["Bezeichnung"] == "Testfall"


def test_pseudonymize_twice_returns_409(client, case_id):
    first = _pseudonymize(client, case_id)
    assert first.status_code == 201
    second = _pseudonymize(client, case_id)
    assert second.status_code == 409


def test_reveal_requires_reveal_permission_not_pseudonymization_permission(client, case_id):
    _pseudonymize(client, case_id)
    response = client.post(
        f"/cases/{case_id}/attributes/SVNR/reveal",
        json={"revealed_by": "dave"},
        headers=PSEUDONYMIZATION_ADMIN_HEADERS,
    )
    assert response.status_code == 403


def test_reveal_404_for_a_not_pseudonymized_attribute(client, case_id):
    response = client.post(
        f"/cases/{case_id}/attributes/SVNR/reveal",
        json={"revealed_by": "dave"},
        headers=REVEAL_ADMIN_HEADERS,
    )
    assert response.status_code == 404


def test_reveal_returns_the_original_value_without_restoring_it_live(client, case_id):
    _pseudonymize(client, case_id, reason="DSGVO-Löschanfrage")

    response = client.post(
        f"/cases/{case_id}/attributes/SVNR/reveal",
        json={"revealed_by": "dave"},
        headers=REVEAL_ADMIN_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["value"] == "123-45-6789"
    assert body["reason"] == "DSGVO-Löschanfrage"
    assert body["pseudonymized_by"] == "carol"

    case = client.get(f"/cases/{case_id}", headers=CASE_HEADERS).json()
    assert case["attributes"]["SVNR"] == "[PSEUDONYMISIERT]"


def test_reveal_updates_last_revealed_tracking_on_the_listing(client, case_id):
    _pseudonymize(client, case_id)
    client.post(
        f"/cases/{case_id}/attributes/SVNR/reveal",
        json={"revealed_by": "dave"},
        headers=REVEAL_ADMIN_HEADERS,
    )

    listing = client.get(f"/cases/{case_id}/attributes/pseudonymized", headers=CASE_HEADERS).json()
    assert len(listing) == 1
    assert listing[0]["last_revealed_by"] == "dave"
    assert listing[0]["last_revealed_at"] is not None


def test_list_pseudonymized_attributes_empty_for_a_case_with_none(client, case_id):
    response = client.get(f"/cases/{case_id}/attributes/pseudonymized", headers=CASE_HEADERS)
    assert response.status_code == 200
    assert response.json() == []


def test_list_pseudonymized_attributes_requires_case_read_permission(client, case_id):
    response = client.get(
        f"/cases/{case_id}/attributes/pseudonymized",
        headers={"X-DMS-Principal": ""},
    )
    assert response.status_code == 401
