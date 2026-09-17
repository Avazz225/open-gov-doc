from auth_service import superuser


def _login_headers(client, test_user) -> tuple[dict[str, str], str]:
    tokens = client.post("/login", json=test_user).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    principal_id = client.get("/me", headers=headers).json()["sub"]
    return headers, principal_id


def test_get_tracking_config_requires_permission(client, test_user):
    headers, principal_id = _login_headers(client, test_user)
    response = client.get(f"/user-tracking-config/{principal_id}", headers=headers)
    assert response.status_code == 403


def test_put_tracking_config_requires_permission(client, test_user):
    headers, principal_id = _login_headers(client, test_user)
    response = client.put(
        f"/user-tracking-config/{principal_id}",
        json={"enabled": True, "updated_by": "tester"},
        headers=headers,
    )
    assert response.status_code == 403


def test_get_tracking_config_defaults_to_disabled_for_an_unconfigured_principal(
    client, test_user, grant_role
):
    headers, principal_id = _login_headers(client, test_user)
    grant_role(principal_id, "domain-admin-user-tracking")

    response = client.get(f"/user-tracking-config/{principal_id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["principal_id"] == principal_id
    assert body["enabled"] is False


def test_put_and_get_tracking_config_roundtrip(client, test_user, grant_role):
    headers, principal_id = _login_headers(client, test_user)
    grant_role(principal_id, "domain-admin-user-tracking")

    put_response = client.put(
        f"/user-tracking-config/{principal_id}",
        json={"enabled": True, "updated_by": "tester"},
        headers=headers,
    )
    assert put_response.status_code == 200
    assert put_response.json()["enabled"] is True
    assert put_response.json()["updated_by"] == "tester"

    get_response = client.get(f"/user-tracking-config/{principal_id}", headers=headers)
    assert get_response.json()["enabled"] is True


def test_login_is_not_tracked_when_disabled(client, test_user, grant_role):
    headers, principal_id = _login_headers(client, test_user)
    grant_role(principal_id, "domain-admin-user-tracking")
    grant_role(principal_id, "domain-admin-user-tracking-view")

    # A second, ordinary login - tracking has never been enabled for this
    # principal (default off).
    client.post("/login", json=test_user)

    sessions = client.get(
        "/user-tracking-sessions", params={"principal_id": principal_id}, headers=headers
    ).json()
    assert sessions == []


def test_login_is_tracked_once_enabled_including_client_ip_and_user_agent(
    client, test_user, grant_role
):
    headers, principal_id = _login_headers(client, test_user)
    grant_role(principal_id, "domain-admin-user-tracking")
    grant_role(principal_id, "domain-admin-user-tracking-view")
    client.put(
        f"/user-tracking-config/{principal_id}",
        json={"enabled": True, "updated_by": "tester"},
        headers=headers,
    )

    login_response = client.post(
        "/login",
        json=test_user,
        headers={"X-DMS-Client-IP": "203.0.113.42", "User-Agent": "pytest-agent/1.0"},
    )
    assert login_response.status_code == 200

    sessions = client.get(
        "/user-tracking-sessions", params={"principal_id": principal_id}, headers=headers
    ).json()
    assert len(sessions) == 1
    session = sessions[0]
    assert session["principal_id"] == principal_id
    assert session["username"] == test_user["username"]
    assert session["event_type"] == "login"
    assert session["auth_method"] == "keycloak"
    assert session["client_ip"] == "203.0.113.42"
    assert session["user_agent"] == "pytest-agent/1.0"


def test_refresh_is_tracked_once_enabled(client, test_user, grant_role):
    headers, principal_id = _login_headers(client, test_user)
    grant_role(principal_id, "domain-admin-user-tracking")
    grant_role(principal_id, "domain-admin-user-tracking-view")
    client.put(
        f"/user-tracking-config/{principal_id}",
        json={"enabled": True, "updated_by": "tester"},
        headers=headers,
    )

    refresh_token = client.post("/login", json=test_user).json()["refresh_token"]
    client.post("/refresh", json={"refresh_token": refresh_token})

    sessions = client.get(
        "/user-tracking-sessions", params={"principal_id": principal_id}, headers=headers
    ).json()
    assert "refresh" in {s["event_type"] for s in sessions}


def test_list_sessions_requires_view_permission_not_config_permission(
    client, test_user, grant_role
):
    headers, principal_id = _login_headers(client, test_user)
    # Only the config (toggle) capability, deliberately NOT the view one.
    grant_role(principal_id, "domain-admin-user-tracking")

    response = client.get(
        "/user-tracking-sessions", params={"principal_id": principal_id}, headers=headers
    )
    assert response.status_code == 403


def test_retention_config_defaults_to_seven_days_and_is_editable(client, test_user, grant_role):
    headers, principal_id = _login_headers(client, test_user)
    grant_role(principal_id, "domain-admin-user-tracking")

    default_response = client.get("/user-tracking-retention-config", headers=headers)
    assert default_response.status_code == 200
    assert default_response.json()["retention_days"] == 7

    put_response = client.put(
        "/user-tracking-retention-config", json={"retention_days": 3}, headers=headers
    )
    assert put_response.status_code == 200
    assert put_response.json()["retention_days"] == 3

    get_response = client.get("/user-tracking-retention-config", headers=headers)
    assert get_response.json()["retention_days"] == 3


def test_retention_config_rejects_less_than_one_day(client, test_user, grant_role):
    headers, principal_id = _login_headers(client, test_user)
    grant_role(principal_id, "domain-admin-user-tracking")

    response = client.put(
        "/user-tracking-retention-config", json={"retention_days": 0}, headers=headers
    )
    assert response.status_code == 422


async def test_activated_superuser_is_tracked_by_default_without_explicit_config(
    session_factory, client, test_user, grant_role
):
    """The concept's own "default active for the activated superuser"
    wording (4.6/5.5) - deliberately verified WITHOUT ever creating a
    `UserTrackingConfig` row for the superuser at all, since the default
    is tied to the live activation state, not a persisted flag (see
    `UserTrackingConfig`'s docstring)."""
    await superuser.activate(session_factory, activation_minutes=30)

    login_response = client.post("/login", json={"username": "superuser", "password": "superuser"})
    assert login_response.status_code == 200
    superuser_principal_id = client.get(
        "/me",
        headers={"Authorization": f"Bearer {login_response.json()['access_token']}"},
    ).json()["sub"]

    # A separate, unrelated principal with the view capability - proves
    # the superuser's tracking took effect independent of anyone
    # explicitly toggling `UserTrackingConfig` for it.
    headers, view_principal_id = _login_headers(client, test_user)
    grant_role(view_principal_id, "domain-admin-user-tracking-view")

    sessions = client.get(
        "/user-tracking-sessions", params={"principal_id": superuser_principal_id}, headers=headers
    ).json()
    assert len(sessions) == 1
    assert sessions[0]["auth_method"] == "technical_account"
