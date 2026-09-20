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
    assert response.json() == {"theme": "auto", "locale": "de"}


def test_update_preferences_persists_theme(test_user):
    with TestClient(app) as client:
        tokens = client.post("/login", json=test_user).json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        update_response = client.put("/me/preferences", json={"theme": "dark"}, headers=headers)
        assert update_response.status_code == 200
        assert update_response.json() == {"theme": "dark", "locale": "de"}

        get_response = client.get("/me/preferences", headers=headers)
        assert get_response.json() == {"theme": "dark", "locale": "de"}


def test_update_preferences_persists_locale(test_user):
    """Phase 47 Session 1 - same round trip as theme, own independent
    Keycloak attribute."""
    with TestClient(app) as client:
        tokens = client.post("/login", json=test_user).json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        update_response = client.put("/me/preferences", json={"locale": "en"}, headers=headers)
        assert update_response.status_code == 200
        assert update_response.json() == {"theme": "auto", "locale": "en"}

        get_response = client.get("/me/preferences", headers=headers)
        assert get_response.json() == {"theme": "auto", "locale": "en"}


def test_update_preferences_theme_does_not_reset_locale(test_user):
    """Regression test for the exact bug `PreferencesUpdate`'s own docstring
    warns about: an existing caller that only ever sends `{"theme": ...}`
    must not silently reset an already-set `locale` back to its default."""
    with TestClient(app) as client:
        tokens = client.post("/login", json=test_user).json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        client.put("/me/preferences", json={"locale": "en"}, headers=headers)
        update_response = client.put("/me/preferences", json={"theme": "dark"}, headers=headers)

    assert update_response.json() == {"theme": "dark", "locale": "en"}


def test_update_preferences_does_not_wipe_other_profile_fields(test_user, keycloak_admin):
    """Regression test for a real bug found live during this session's own
    end-to-end verification: `admin.update_user(user_id, {"attributes":
    ...})` isn't a merge at the Keycloak protocol level - `PUT /admin/
    realms/{realm}/users/{id}` treats the body as the full representation,
    so sending only `attributes` silently wiped `firstName`/`lastName`/
    `email` on every single preference change (present since P4-S6's
    `set_theme_preference`, only now actually exercised against a user
    profile with those fields set). Fixed by spreading the just-read full
    representation before overriding `attributes` (see `admin_users.
    set_theme_preference`'s own docstring)."""
    with TestClient(app) as client:
        tokens = client.post("/login", json=test_user).json()
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        client.put("/me/preferences", json={"theme": "dark"}, headers=headers)
        client.put("/me/preferences", json={"locale": "en"}, headers=headers)

        users = keycloak_admin.get_users(query={"username": test_user["username"], "exact": True})
        profile = keycloak_admin.get_user(users[0]["id"])

    assert profile["firstName"] == "Test"
    assert profile["lastName"] == "User"
    assert profile["email"] == f"{test_user['username']}@example.com"


def test_update_preferences_rejects_unknown_locale(test_user):
    with TestClient(app) as client:
        tokens = client.post("/login", json=test_user).json()
        response = client.put(
            "/me/preferences",
            json={"locale": "fr"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 422


def test_update_preferences_rejects_unknown_theme(test_user):
    with TestClient(app) as client:
        tokens = client.post("/login", json=test_user).json()
        response = client.put(
            "/me/preferences",
            json={"theme": "rainbow"},
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )

    assert response.status_code == 422


def test_get_preferences_for_a_technical_account_returns_defaults_instead_of_crashing(
    client, domain_admin_auth_headers
):
    """Regression test (Phase 51 Session 2, found live in Phase 49 Session
    3): a `TechnicalAccount`-authenticated caller (`users-admin` here) has
    no corresponding Keycloak user at all - `user["sub"]` is the account's
    own local integer row id, not a Keycloak UUID - so the previous
    unconditional `KeycloakAdmin.get_user()` call `500`d on every single
    admin-ui page load while logged in as such an account (every page loads
    the theme/locale switcher). Reuses the `client`/`domain_admin_auth_
    headers` fixtures instead of a second `TestClient(app)` - two separate
    app lifespans in the same test double-subscribe the same NATS durable
    consumer, the exact conflict `domain_admin_auth_headers` itself already
    avoids by depending on `client` rather than creating its own."""
    response = client.get("/me/preferences", headers=domain_admin_auth_headers)

    assert response.status_code == 200
    assert response.json() == {"theme": "auto", "locale": "de"}


def test_update_preferences_for_a_technical_account_echoes_the_request_instead_of_crashing(
    client, domain_admin_auth_headers
):
    """Same fix, the `PUT` side - echoes back what was actually requested
    (not silently substituting the defaults), since a caller that just
    asked to set `theme="dark"` should not see the response claim
    `theme="auto"`, even though nothing is actually persisted server-side
    for a technical account."""
    response = client.put(
        "/me/preferences", json={"theme": "dark"}, headers=domain_admin_auth_headers
    )

    assert response.status_code == 200
    assert response.json() == {"theme": "dark", "locale": "de"}


def test_preferences_reject_missing_token():
    with TestClient(app) as client:
        response = client.get("/me/preferences")

    assert response.status_code == 401


def test_login_rejects_non_superuser_during_maintenance_mode(test_user):
    """Not-Shutdown (4.8, P6-S6): das Gateway broadcastet den Status per
    Header, ohne Gateway (wie hier im Test) wird er direkt gesetzt."""
    with TestClient(app) as client:
        response = client.post(
            "/login", json=test_user, headers={"X-DMS-Maintenance-Active": "true"}
        )

    assert response.status_code == 503


def test_login_allows_superuser_username_during_maintenance_mode():
    """Der eigentliche Login schlägt trotzdem fehl (Superuser-Konto ist per
    Default deaktiviert/hat kein bekanntes Passwort in diesem Test) - dieser
    Test bestätigt nur, dass die Wartungsmodus-Sperre selbst NICHT greift,
    also `401` statt `503` zurückkommt."""
    with TestClient(app) as client:
        response = client.post(
            "/login",
            json={"username": "superuser", "password": "wrong-password"},
            headers={"X-DMS-Maintenance-Active": "true"},
        )

    assert response.status_code == 401


def test_login_unaffected_when_maintenance_mode_inactive(test_user):
    with TestClient(app) as client:
        response = client.post(
            "/login", json=test_user, headers={"X-DMS-Maintenance-Active": "false"}
        )

    assert response.status_code == 200


def test_superuser_status_endpoint_includes_principal_id():
    with TestClient(app) as client:
        response = client.get("/superuser/status")

    assert response.status_code == 200
    assert response.json()["principal_id"] is not None
