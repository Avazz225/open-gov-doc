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
def authorized_principal():
    with httpx.Client(base_url=settings.permission_service_base_url, timeout=10.0) as pc:
        roles = pc.get("/roles").json()
        role = next(r for r in roles if r["name"] == "domain-admin-users")
        assignment = pc.post(
            "/role-assignments",
            json={
                "principal_type": "service",
                "principal_id": REALM_ROLES_TEST_PRINCIPAL,
                "role_id": role["id"],
                "resource_id": "root",
            },
        ).json()
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
