import uuid


def _user_payload(**overrides):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"lookup-test-{suffix}",
        "email": f"lookup-test-{suffix}@example.com",
        "password": "testpass123",
        "first_name": "Lookup",
        "last_name": "Test",
    }
    payload.update(overrides)
    return payload


def test_lookup_user_requires_authentication(client):
    response = client.get("/users/lookup", params={"username": "does-not-matter"})
    assert response.status_code == 401


def test_lookup_user_works_for_a_regular_authenticated_user(client, domain_admin_auth_headers):
    """`GET /users/lookup` ist bewusst NICHT hinter `admin.user_management`
    gegated (anders als `GET /users`, siehe `main.py`) - jeder authentifizierte
    Nutzer muss einen anderen per Username auflösen können, um ihn z. B. in
    einen Teamspace einzuladen (2.5, P14-S6). Seit P19-S3 über die
    "everyone"-Gruppe geprüft (ADR 0068) statt komplett offen zu sein - da
    die "everyone"-Rolle jedem authentifizierten Principal implizit
    `users.lookup` gewährt, ändert sich am beobachtbaren Verhalten hier
    nichts (siehe `test_lookup_user_returns_403_without_users_lookup_permission`
    für den Negativfall)."""
    caller_payload = _user_payload()
    target_payload = _user_payload()
    caller = client.post("/users", json=caller_payload, headers=domain_admin_auth_headers).json()
    target = client.post("/users", json=target_payload, headers=domain_admin_auth_headers).json()

    caller_login = client.post(
        "/login",
        json={"username": caller_payload["username"], "password": caller_payload["password"]},
    ).json()
    caller_headers = {"Authorization": f"Bearer {caller_login['access_token']}"}

    response = client.get(
        "/users/lookup", params={"username": target_payload["username"]}, headers=caller_headers
    )

    assert response.status_code == 200
    assert response.json() == {"id": target["id"], "username": target_payload["username"]}
    # Keine E-Mail/Namen/Freigabestatus wie bei GET /users - siehe admin_users.py.
    assert set(response.json().keys()) == {"id", "username"}

    client.delete(f"/users/{caller['id']}", headers=domain_admin_auth_headers)
    client.delete(f"/users/{target['id']}", headers=domain_admin_auth_headers)


def test_lookup_unknown_username_returns_404(client, domain_admin_auth_headers):
    response = client.get(
        "/users/lookup",
        params={"username": "definitely-does-not-exist"},
        headers=domain_admin_auth_headers,
    )
    assert response.status_code == 404


def test_lookup_user_returns_403_without_users_lookup_permission(
    client, test_user, everyone_role_without
):
    """Beweist, dass P19-S3s Gate tatsächlich gegen permission-service prüft
    (ADR 0068), nicht nur `Depends(get_current_user)`: entzieht der
    "everyone"-Rolle gezielt `users.lookup` für die Dauer dieses Tests."""
    everyone_role_without("users.lookup")
    login = client.post(
        "/login", json={"username": test_user["username"], "password": test_user["password"]}
    ).json()
    headers = {"Authorization": f"Bearer {login['access_token']}"}

    response = client.get(
        "/users/lookup", params={"username": test_user["username"]}, headers=headers
    )

    assert response.status_code == 403
