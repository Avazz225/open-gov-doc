import pytest
from fastapi.testclient import TestClient
from object_type_service.main import app

RECHNUNG_PAYLOAD = {
    "name": "Rechnung",
    "applies_to": "document",
    "attributes": [
        {"name": "Rechnungsnummer", "type": "string", "required": True, "pattern": r"RE-\d{6}"},
        {"name": "Betrag", "type": "decimal", "required": True},
    ],
    "conditions": [{"if": "Betrag > 10000", "then": "require:Kostenstelle"}],
}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "object-type-service"


def test_create_and_get(client):
    create_response = client.post("/object-types", json=RECHNUNG_PAYLOAD)
    assert create_response.status_code == 201
    object_type_id = create_response.json()["id"]

    get_response = client.get(f"/object-types/{object_type_id}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "Rechnung"


def test_duplicate_name_returns_409(client):
    client.post("/object-types", json=RECHNUNG_PAYLOAD)
    response = client.post("/object-types", json=RECHNUNG_PAYLOAD)
    assert response.status_code == 409


def test_get_unknown_returns_404(client):
    response = client.get("/object-types/999999")
    assert response.status_code == 404


def test_list_filters_by_applies_to(client):
    client.post("/object-types", json=RECHNUNG_PAYLOAD)
    client.post(
        "/object-types", json={"name": "Projektordner", "applies_to": "folder", "attributes": []}
    )

    response = client.get("/object-types", params={"applies_to": "folder"})
    names = {o["name"] for o in response.json()}
    assert names == {"Projektordner"}


def test_validate_endpoint_reports_errors(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]

    invalid = client.post(
        f"/object-types/{object_type_id}/validate",
        json={"name": "irrelevant.pdf", "attributes": {}},
    )
    assert invalid.status_code == 200
    assert invalid.json()["valid"] is False
    assert len(invalid.json()["errors"]) > 0

    valid = client.post(
        f"/object-types/{object_type_id}/validate",
        json={
            "name": "RE-123456.pdf",
            "attributes": {"Rechnungsnummer": "RE-123456", "Betrag": 5},
        },
    )
    assert valid.json() == {"valid": True, "errors": []}


def test_validate_conditional_requirement(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]

    response = client.post(
        f"/object-types/{object_type_id}/validate",
        json={
            "name": "RE-123456.pdf",
            "attributes": {"Rechnungsnummer": "RE-123456", "Betrag": 50000},
        },
    )
    assert response.json()["valid"] is False
    assert any("Kostenstelle" in e for e in response.json()["errors"])


def test_validate_unknown_object_type_returns_404(client):
    response = client.post("/object-types/999999/validate", json={"name": "x", "attributes": {}})
    assert response.status_code == 404


def test_update_and_delete(client):
    object_type_id = client.post("/object-types", json=RECHNUNG_PAYLOAD).json()["id"]

    update_response = client.put(
        f"/object-types/{object_type_id}",
        json={"attributes": [{"name": "Neu", "type": "string"}], "conditions": []},
    )
    assert update_response.status_code == 200
    assert len(update_response.json()["attributes"]) == 1

    delete_response = client.delete(f"/object-types/{object_type_id}")
    assert delete_response.status_code == 204
    assert client.get(f"/object-types/{object_type_id}").status_code == 404
