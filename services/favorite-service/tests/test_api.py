import pytest
from fastapi.testclient import TestClient
from favorite_service.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "favorite-service"


def test_create_and_list(client):
    create_response = client.post(
        "/favorites", json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"}
    )
    assert create_response.status_code == 201
    body = create_response.json()
    assert body["user_id"] == "alice"
    assert body["object_type"] == "document"
    assert body["object_id"] == "doc-1"

    list_response = client.get("/favorites", params={"user_id": "alice"})
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


def test_duplicate_returns_409(client):
    payload = {"user_id": "alice", "object_type": "folder", "object_id": "folder-1"}
    client.post("/favorites", json=payload)
    response = client.post("/favorites", json=payload)
    assert response.status_code == 409


def test_list_filters_by_object_type(client):
    client.post(
        "/favorites", json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"}
    )
    client.post(
        "/favorites", json={"user_id": "alice", "object_type": "folder", "object_id": "folder-1"}
    )

    response = client.get("/favorites", params={"user_id": "alice", "object_type": "folder"})
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["object_type"] == "folder"


def test_list_scoped_to_user(client):
    client.post(
        "/favorites", json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"}
    )
    client.post(
        "/favorites", json={"user_id": "bob", "object_type": "document", "object_id": "doc-2"}
    )

    response = client.get("/favorites", params={"user_id": "bob"})
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["object_id"] == "doc-2"


def test_delete_removes_favorite(client):
    client.post(
        "/favorites", json={"user_id": "alice", "object_type": "document", "object_id": "doc-1"}
    )

    delete_response = client.request(
        "DELETE",
        "/favorites",
        params={"user_id": "alice", "object_type": "document", "object_id": "doc-1"},
    )
    assert delete_response.status_code == 204

    list_response = client.get("/favorites", params={"user_id": "alice"})
    assert list_response.json() == []


def test_delete_unknown_returns_404(client):
    response = client.request(
        "DELETE",
        "/favorites",
        params={"user_id": "alice", "object_type": "document", "object_id": "unknown"},
    )
    assert response.status_code == 404
