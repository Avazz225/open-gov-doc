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
        response = client.get("/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})

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


def test_get_preferences_defaults_to_auto(test_user):
    with TestClient(app) as client:
        tokens = client.post("/login", json=test_user).json()
        response = client.get(
            "/me/preferences", headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )

    assert response.status_code == 200
    assert response.json() == {"theme": "auto"}


def test_update_preferences_persists_theme(test_user):
    with TestClient(app) as client:
        tokens = client.post("/login", json=test_user).json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        update_response = client.put("/me/preferences", json={"theme": "dark"}, headers=headers)
        assert update_response.status_code == 200
        assert update_response.json() == {"theme": "dark"}

        get_response = client.get("/me/preferences", headers=headers)
        assert get_response.json() == {"theme": "dark"}


def test_update_preferences_rejects_unknown_theme(test_user):
    with TestClient(app) as client:
        tokens = client.post("/login", json=test_user).json()
        response = client.put(
            "/me/preferences",
            json={"theme": "rainbow"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 422


def test_preferences_reject_missing_token():
    with TestClient(app) as client:
        response = client.get("/me/preferences")

    assert response.status_code == 401
