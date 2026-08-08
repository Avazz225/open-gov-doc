"""Interne, ungegatete Endpunkte fuer `license-service`s Nutzungspruefung
(9.1, seit P9-S1) - `GET /users/count`/`GET /sessions/count`. Kein Service
hat einen echten Keycloak-Bearer-Token fuer `Depends(get_current_user)`,
daher bewusst ohne Authentifizierung (siehe main.py-Docstrings)."""


def test_count_users_does_not_require_authentication(client):
    response = client.get("/users/count")

    assert response.status_code == 200
    assert isinstance(response.json()["count"], int)


def test_count_users_reflects_created_user(client, domain_admin_auth_headers):
    before = client.get("/users/count").json()["count"]
    created = client.post(
        "/users",
        json={
            "username": "count-test-user",
            "email": "count-test-user@example.com",
            "password": "testpass123",
            "first_name": "Count",
            "last_name": "Test",
        },
        headers=domain_admin_auth_headers,
    ).json()

    after = client.get("/users/count").json()["count"]

    assert after == before + 1
    client.delete(f"/users/{created['id']}", headers=domain_admin_auth_headers)


def test_count_sessions_does_not_require_authentication(client):
    response = client.get("/sessions/count")

    assert response.status_code == 200
    assert isinstance(response.json()["count"], int)


def test_count_sessions_reflects_active_login(client, test_user):
    before = client.get("/sessions/count").json()["count"]
    client.post(
        "/login", json={"username": test_user["username"], "password": test_user["password"]}
    ).raise_for_status()

    after = client.get("/sessions/count").json()["count"]

    assert after >= before + 1
