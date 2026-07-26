import httpx
import pytest
from fastapi.testclient import TestClient
from folder_service.main import app
from folder_service.settings import Settings

OBJECT_TYPE_SERVICE_URL = Settings().object_type_service_base_url


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def object_type_id():
    with httpx.Client(base_url=OBJECT_TYPE_SERVICE_URL, timeout=10.0) as oc:
        response = oc.post(
            "/object-types",
            json={
                "name": "Projektordner",
                "applies_to": "folder",
                "attributes": [{"name": "Projektnummer", "type": "string", "required": True}],
            },
        )
        response.raise_for_status()
        type_id = response.json()["id"]
        yield type_id
        oc.delete(f"/object-types/{type_id}")


def test_create_folder_with_invalid_attributes_is_rejected(client, object_type_id):
    response = client.post(
        "/folders",
        json={"name": "Projekt X", "created_by": "alice", "object_type_id": object_type_id},
    )
    assert response.status_code == 400
    assert "Projektnummer" in str(response.json()["detail"])


def test_create_folder_with_valid_attributes_succeeds(client, object_type_id):
    response = client.post(
        "/folders",
        json={
            "name": "Projekt X",
            "created_by": "alice",
            "object_type_id": object_type_id,
            "attributes": {"Projektnummer": "P-001"},
        },
    )
    assert response.status_code == 201
