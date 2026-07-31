import uuid


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


def test_users_endpoint_requires_authentication(client):
    response = client.post("/users", json=_user_payload())
    assert response.status_code == 401


def test_users_endpoint_requires_user_management_capability(client, domain_admin_auth_headers):
    payload = _user_payload()
    regular_user = client.post("/users", json=payload, headers=domain_admin_auth_headers).json()
    login = client.post(
        "/login", json={"username": payload["username"], "password": payload["password"]}
    ).json()

    response = client.get("/users", headers={"Authorization": f"Bearer {login['access_token']}"})

    assert response.status_code == 403
    client.delete(f"/users/{regular_user['id']}", headers=domain_admin_auth_headers)


def test_create_and_list_user(client, domain_admin_auth_headers):
    payload = _user_payload()
    create_response = client.post("/users", json=payload, headers=domain_admin_auth_headers)
    assert create_response.status_code == 201
    created = create_response.json()
    assert created["username"] == payload["username"]
    assert created["enabled"] is True

    list_response = client.get("/users", headers=domain_admin_auth_headers)
    assert list_response.status_code == 200
    usernames = [u["username"] for u in list_response.json()]
    assert payload["username"] in usernames

    client.delete(f"/users/{created['id']}", headers=domain_admin_auth_headers)


def test_create_duplicate_username_returns_409(client, domain_admin_auth_headers):
    payload = _user_payload()
    first = client.post("/users", json=payload, headers=domain_admin_auth_headers)
    assert first.status_code == 201

    second = client.post("/users", json=payload, headers=domain_admin_auth_headers)
    assert second.status_code == 409

    client.delete(f"/users/{first.json()['id']}", headers=domain_admin_auth_headers)


def test_created_user_can_log_in(client, domain_admin_auth_headers):
    payload = _user_payload()
    created = client.post("/users", json=payload, headers=domain_admin_auth_headers).json()

    login_response = client.post(
        "/login", json={"username": payload["username"], "password": payload["password"]}
    )
    assert login_response.status_code == 200
    assert "access_token" in login_response.json()

    client.delete(f"/users/{created['id']}", headers=domain_admin_auth_headers)


def test_delete_user_removes_it(client, domain_admin_auth_headers):
    payload = _user_payload()
    created = client.post("/users", json=payload, headers=domain_admin_auth_headers).json()

    delete_response = client.delete(f"/users/{created['id']}", headers=domain_admin_auth_headers)
    assert delete_response.status_code == 204

    remaining = client.get("/users", headers=domain_admin_auth_headers).json()
    usernames = [u["username"] for u in remaining]
    assert payload["username"] not in usernames


def test_delete_unknown_user_returns_404(client, domain_admin_auth_headers):
    response = client.delete("/users/does-not-exist", headers=domain_admin_auth_headers)
    assert response.status_code == 404
