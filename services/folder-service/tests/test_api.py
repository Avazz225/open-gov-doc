import pytest
from fastapi.testclient import TestClient
from folder_service.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "folder-service"


def test_root_folder_exists(client):
    response = client.get("/folders/root")
    assert response.status_code == 200
    assert response.json()["parent_id"] is None


def test_create_folder_defaults_to_root_parent(client):
    response = client.post("/folders", json={"name": "Projekte", "created_by": "alice"})
    assert response.status_code == 201
    assert response.json()["parent_id"] == "root"


def test_create_folder_unknown_parent_returns_404(client):
    response = client.post(
        "/folders", json={"name": "X", "parent_id": "nope", "created_by": "alice"}
    )
    assert response.status_code == 404


def test_list_children(client):
    created = client.post("/folders", json={"name": "Projekte", "created_by": "alice"}).json()

    response = client.get("/folders/root/children")
    assert response.status_code == 200
    assert created["id"] in {f["id"] for f in response.json()}


def test_update_rename(client):
    created = client.post("/folders", json={"name": "Alt", "created_by": "alice"}).json()

    response = client.patch(f"/folders/{created['id']}", json={"name": "Neu"})
    assert response.status_code == 200
    assert response.json()["name"] == "Neu"


def test_move_to_self_returns_400(client):
    created = client.post("/folders", json={"name": "X", "created_by": "alice"}).json()

    response = client.patch(f"/folders/{created['id']}", json={"parent_id": created["id"]})
    assert response.status_code == 400


def test_delete_non_empty_folder_returns_409(client):
    parent = client.post("/folders", json={"name": "Parent", "created_by": "alice"}).json()
    client.post(
        "/folders", json={"name": "Child", "parent_id": parent["id"], "created_by": "alice"}
    )

    response = client.delete(f"/folders/{parent['id']}")
    assert response.status_code == 409


def test_delete_empty_folder(client):
    created = client.post("/folders", json={"name": "Leer", "created_by": "alice"}).json()

    response = client.delete(f"/folders/{created['id']}")
    assert response.status_code == 204
    assert client.get(f"/folders/{created['id']}").status_code == 404
