from auth_service.main import app
from fastapi.testclient import TestClient


def test_login_returns_tokens(test_user):
    with TestClient(app) as client:
        response = client.post("/login", json=test_user)

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in body


def test_login_wrong_password_returns_401(test_user):
    with TestClient(app) as client:
        response = client.post(
            "/login", json={"username": test_user["username"], "password": "wrong"}
        )

    assert response.status_code == 401


def test_me_returns_identity_for_valid_token(test_user):
    with TestClient(app) as client:
        tokens = client.post("/login", json=test_user).json()
        response = client.get(
            "/me", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )

    assert response.status_code == 200
    body = response.json()
    assert body["username"] == test_user["username"]


def test_me_rejects_missing_token():
    with TestClient(app) as client:
        response = client.get("/me")

    assert response.status_code == 401


def test_refresh_returns_new_tokens(test_user):
    with TestClient(app) as client:
        tokens = client.post("/login", json=test_user).json()
        response = client.post("/refresh", json={"refresh_token": tokens["refresh_token"]})

    assert response.status_code == 200
    assert "access_token" in response.json()


def test_refresh_with_invalid_token_returns_401():
    with TestClient(app) as client:
        response = client.post("/refresh", json={"refresh_token": "not-a-real-token"})

    assert response.status_code == 401


def test_healthz():
    with TestClient(app) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["service"] == "auth-service"
