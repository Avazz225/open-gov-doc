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
    einen Teamspace einzuladen (2.5, P14-S6)."""
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
