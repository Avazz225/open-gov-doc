import os
import uuid

import httpx

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
TEAMSPACE_SERVICE_URL = os.environ.get("TEST_TEAMSPACE_SERVICE_URL", "http://localhost:8032")


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


def test_delete_user_revokes_role_assignments(client, domain_admin_auth_headers):
    """P55-S2 (permission_client.revoke_all_role_assignments) - real round
    trip against the real, live-running `permission-service`, same
    convention as every other test in this file (Keycloak)."""
    payload = _user_payload()
    created = client.post("/users", json=payload, headers=domain_admin_auth_headers).json()
    user_id = created["id"]

    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=10.0) as pc:
        roles = pc.get("/roles").json()
        role_id = roles[0]["id"]
        grant = pc.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": user_id,
                "role_id": role_id,
                "resource_id": "root",
            },
        )
        grant.raise_for_status()
        before = pc.get("/role-assignments", params={"principal_id": user_id}).json()
        assert len(before) == 1

    delete_response = client.delete(f"/users/{user_id}", headers=domain_admin_auth_headers)
    assert delete_response.status_code == 204

    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=10.0) as pc:
        after = pc.get("/role-assignments", params={"principal_id": user_id}).json()
        assert after == []


def test_delete_user_removes_teamspace_memberships(client, domain_admin_auth_headers):
    """P55-S2 (teamspace_client.delete_memberships) - real round trip
    against the real, live-running `teamspace-service` (which itself
    calls the real `folder-service`/`permission-service` to create the
    teamspace, same "no mocking" convention as
    `teamspace-service/tests/test_api.py`). A second, fixed cleanup
    principal is invited as a manager BEFORE the deleted user's own
    membership is removed - without it, this test would leave a
    permanently unmanageable teamspace behind (the sole member gone, no
    one left who could delete it via the normal API), same cleanup
    discipline `teamspace-service/tests/test_api.py`'s own
    `_cleanup_teamspace_folders` fixture already established for a
    different reason."""
    cleanup_principal = "auth-service-test-teamspace-cleanup"
    payload = _user_payload()
    created = client.post("/users", json=payload, headers=domain_admin_auth_headers).json()
    user_id = created["id"]

    with httpx.Client(base_url=TEAMSPACE_SERVICE_URL, timeout=30.0) as tc:
        create_response = tc.post(
            "/teamspaces",
            json={"name": "P55-S2 Verify", "description": ""},
            headers={"X-DMS-Principal": user_id},
        )
        assert create_response.status_code == 201
        teamspace_id = create_response.json()["id"]
        invite_response = tc.post(
            f"/teamspaces/{teamspace_id}/members",
            json={"principal_id": cleanup_principal, "can_manage_members": True},
            headers={"X-DMS-Principal": user_id},
        )
        assert invite_response.status_code == 201

    try:
        delete_response = client.delete(f"/users/{user_id}", headers=domain_admin_auth_headers)
        assert delete_response.status_code == 204

        with httpx.Client(base_url=TEAMSPACE_SERVICE_URL, timeout=10.0) as tc:
            members = tc.get(
                f"/teamspaces/{teamspace_id}/members",
                headers={"X-DMS-Principal": cleanup_principal},
            ).json()
            assert user_id not in {m["principal_id"] for m in members}
    finally:
        with httpx.Client(base_url=TEAMSPACE_SERVICE_URL, timeout=30.0) as tc:
            tc.delete(f"/teamspaces/{teamspace_id}", headers={"X-DMS-Principal": cleanup_principal})
