import uuid

from auth_service.main import app
from fastapi.testclient import TestClient


def _user_payload(**overrides):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"admin-test-{suffix}",
        "email": f"admin-test-{suffix}@example.com",
        "password": "testpass123",
        "first_name": "Admin",
        "last_name": "Test",
    }
    payload.update(overrides)
    return payload


def test_create_and_list_user():
    with TestClient(app) as client:
        payload = _user_payload()
        create_response = client.post("/users", json=payload)
        assert create_response.status_code == 201
        created = create_response.json()
        assert created["username"] == payload["username"]
        assert created["enabled"] is True

        list_response = client.get("/users")
        assert list_response.status_code == 200
        usernames = [u["username"] for u in list_response.json()]
        assert payload["username"] in usernames

        client.delete(f"/users/{created['id']}")


def test_create_duplicate_username_returns_409():
    with TestClient(app) as client:
        payload = _user_payload()
        first = client.post("/users", json=payload)
        assert first.status_code == 201

        second = client.post("/users", json=payload)
        assert second.status_code == 409

        client.delete(f"/users/{first.json()['id']}")


def test_created_user_can_log_in():
    with TestClient(app) as client:
        payload = _user_payload()
        created = client.post("/users", json=payload).json()

        login_response = client.post(
            "/login", json={"username": payload["username"], "password": payload["password"]}
        )
        assert login_response.status_code == 200
        assert "access_token" in login_response.json()

        client.delete(f"/users/{created['id']}")


def test_delete_user_removes_it():
    with TestClient(app) as client:
        payload = _user_payload()
        created = client.post("/users", json=payload).json()

        delete_response = client.delete(f"/users/{created['id']}")
        assert delete_response.status_code == 204

        usernames = [u["username"] for u in client.get("/users").json()]
        assert payload["username"] not in usernames


def test_delete_unknown_user_returns_404():
    with TestClient(app) as client:
        response = client.delete("/users/does-not-exist")
        assert response.status_code == 404
