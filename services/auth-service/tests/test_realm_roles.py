import uuid

import httpx
import pytest
from auth_service.settings import Settings

settings = Settings()

# Testprinzipal, dem einzelne Tests gezielt `domain-admin-users` zuweisen/
# entziehen (Gate von `POST /realm-roles`) - gleiches Muster wie
# config-service's `tests/conftest.py::authorized_principal`.
REALM_ROLES_TEST_PRINCIPAL = "auth-service-realm-roles-tests"


@pytest.fixture
def authorized_principal(role_assignment_immediate):
    with httpx.Client(base_url=settings.permission_service_base_url, timeout=10.0) as pc:
        roles = pc.get("/roles").json()
        role = next(r for r in roles if r["name"] == "domain-admin-users")
        # Seit P17-S3 (4.3/14.2) liefert `POST /role-assignments` das
        # gegatete `RoleAssignmentActionResult` - `role_assignment_immediate`
        # setzt die Genehmigungspflicht für diesen Test aus, damit
        # `role_assignment` hier garantiert sofort gesetzt ist (statt nur
        # `pending_approval`), siehe permission_service/schemas.py.
        assignment = pc.post(
            "/role-assignments",
            json={
                "principal_type": "service",
                "principal_id": REALM_ROLES_TEST_PRINCIPAL,
                "role_id": role["id"],
                "resource_id": "root",
            },
        ).json()["role_assignment"]
        yield REALM_ROLES_TEST_PRINCIPAL
        pc.delete(f"/role-assignments/{assignment['id']}")


def test_list_realm_roles_includes_bootstrapped_dms_admin(client):
    """`_ensure_dms_admin_role` legt `dms-admin` bereits beim Start an (P5e-S2)
    - `GET /realm-roles` muss sie als eine der vorhandenen Rollen zeigen."""
    response = client.get("/realm-roles")
    assert response.status_code == 200
    names = [r["name"] for r in response.json()]
    assert "dms-admin" in names


def test_list_realm_roles_excludes_keycloak_builtins(client):
    response = client.get("/realm-roles")
    assert response.status_code == 200
    names = [r["name"] for r in response.json()]
    assert "offline_access" not in names
    assert "uma_authorization" not in names
    assert not any(name.startswith("default-roles-") for name in names)


def test_ensure_realm_roles_without_principal_returns_403(client):
    response = client.post("/realm-roles", json={"names": ["irrelevant"]})
    assert response.status_code == 403


def test_ensure_realm_roles_with_unauthorized_principal_returns_403(client):
    response = client.post(
        "/realm-roles",
        json={"names": ["irrelevant"]},
        headers={"X-DMS-Principal": "irgendein-principal-ohne-rolle"},
    )
    assert response.status_code == 403


def test_ensure_realm_roles_creates_new_role_idempotently(client, authorized_principal):
    role_name = f"realm-role-test-{uuid.uuid4().hex[:8]}"
    first = client.post(
        "/realm-roles",
        json={"names": [role_name]},
        headers={"X-DMS-Principal": authorized_principal},
    )
    assert first.status_code == 204

    listed = client.get("/realm-roles").json()
    assert role_name in [r["name"] for r in listed]

    # Zweiter Aufruf mit demselben Namen darf nicht scheitern
    # (`skip_exists=True`, gleiches Primitiv wie `dms-admin`-Bootstrap).
    second = client.post(
        "/realm-roles",
        json={"names": [role_name]},
        headers={"X-DMS-Principal": authorized_principal},
    )
    assert second.status_code == 204


# Phase 78 Session 3 - `POST /realm-roles` selbst schafft nur die Rolle
# (siehe deren eigenes Docstring: "assignment continues to happen outside
# this service via the Keycloak Admin Console"). Die folgenden Tests decken
# die neue, tatsächliche Zuweisung ab.


def test_assign_realm_role_endpoints_require_authentication(client):
    response = client.post("/realm-roles/dms-admin/assignments", json={"username": "irrelevant"})
    assert response.status_code == 401


def test_assign_realm_role_requires_user_management_capability(client, domain_admin_auth_headers):
    payload = {
        "username": f"assign-target-{uuid.uuid4().hex[:8]}",
        "email": "assign-target@example.com",
        "password": "testpass123",
        "first_name": "Assign",
        "last_name": "Target",
    }
    created = client.post("/users", json=payload, headers=domain_admin_auth_headers).json()
    login = client.post(
        "/login", json={"username": payload["username"], "password": payload["password"]}
    ).json()

    response = client.post(
        "/realm-roles/dms-admin/assignments",
        json={"username": payload["username"]},
        headers={"Authorization": f"Bearer {login['access_token']}"},
    )

    assert response.status_code == 403
    client.delete(f"/users/{created['id']}", headers=domain_admin_auth_headers)


def test_assign_realm_role_to_unknown_role_returns_404(client, domain_admin_auth_headers):
    response = client.post(
        "/realm-roles/does-not-exist-role/assignments",
        json={"username": "irrelevant"},
        headers=domain_admin_auth_headers,
    )
    assert response.status_code == 404


def test_assign_realm_role_to_unknown_user_returns_404(
    client, domain_admin_auth_headers, authorized_principal
):
    role_name = f"realm-role-test-{uuid.uuid4().hex[:8]}"
    ensure = client.post(
        "/realm-roles",
        json={"names": [role_name]},
        headers={"X-DMS-Principal": authorized_principal},
    )
    assert ensure.status_code == 204

    response = client.post(
        f"/realm-roles/{role_name}/assignments",
        json={"username": "no-such-user-xyz"},
        headers=domain_admin_auth_headers,
    )
    assert response.status_code == 404


def test_assign_list_and_unassign_realm_role_roundtrip(
    client, domain_admin_auth_headers, authorized_principal
):
    """Live roundtrip: assign an existing role to a real user, confirm it
    shows up in `GET .../assignments`, then unassign it and confirm it's
    gone again - proves the actual Keycloak-side effect, not just that the
    endpoints return 2xx."""
    role_name = f"realm-role-test-{uuid.uuid4().hex[:8]}"
    assert (
        client.post(
            "/realm-roles",
            json={"names": [role_name]},
            headers={"X-DMS-Principal": authorized_principal},
        ).status_code
        == 204
    )

    user_payload = {
        "username": f"realm-role-assignee-{uuid.uuid4().hex[:8]}",
        "email": "realm-role-assignee@example.com",
        "password": "testpass123",
        "first_name": "Realm",
        "last_name": "Assignee",
    }
    created = client.post("/users", json=user_payload, headers=domain_admin_auth_headers).json()

    try:
        assign = client.post(
            f"/realm-roles/{role_name}/assignments",
            json={"username": user_payload["username"]},
            headers=domain_admin_auth_headers,
        )
        assert assign.status_code == 204

        members = client.get(
            f"/realm-roles/{role_name}/assignments", headers=domain_admin_auth_headers
        )
        assert members.status_code == 200
        assert user_payload["username"] in [m["username"] for m in members.json()]

        unassign = client.delete(
            f"/realm-roles/{role_name}/assignments/{user_payload['username']}",
            headers=domain_admin_auth_headers,
        )
        assert unassign.status_code == 204

        members_after = client.get(
            f"/realm-roles/{role_name}/assignments", headers=domain_admin_auth_headers
        )
        assert user_payload["username"] not in [m["username"] for m in members_after.json()]
    finally:
        client.delete(f"/users/{created['id']}", headers=domain_admin_auth_headers)


def test_list_realm_role_assignments_for_unknown_role_returns_404(
    client, domain_admin_auth_headers
):
    response = client.get(
        "/realm-roles/does-not-exist-role/assignments", headers=domain_admin_auth_headers
    )
    assert response.status_code == 404
