import uuid

import httpx
import pytest
from auth_service.settings import Settings

settings = Settings()

# Same pattern as `test_realm_roles.py::authorized_principal` - a dedicated
# test principal that individual tests grant/withhold `service-user-lookup`
# for (Phase 50 Session 2, the new `service.user_lookup` capability).
SERVICE_DIRECTORY_TEST_PRINCIPAL = "auth-service-service-directory-tests"


def _user_payload(**overrides):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"service-directory-test-{suffix}",
        "email": f"service-directory-test-{suffix}@example.com",
        "password": "testpass123",
        "first_name": "Verzeichnis",
        "last_name": f"Test{suffix}",
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def authorized_service_principal(role_assignment_immediate):
    with httpx.Client(base_url=settings.permission_service_base_url, timeout=10.0) as pc:
        roles = pc.get("/roles").json()
        role = next(r for r in roles if r["name"] == "service-user-lookup")
        assignment = pc.post(
            "/role-assignments",
            json={
                "principal_type": "service",
                "principal_id": SERVICE_DIRECTORY_TEST_PRINCIPAL,
                "role_id": role["id"],
                "resource_id": "root",
            },
        ).json()["role_assignment"]
        yield SERVICE_DIRECTORY_TEST_PRINCIPAL
        pc.delete(f"/role-assignments/{assignment['id']}")


def test_service_directory_without_principal_returns_403(client):
    response = client.get("/users/service-directory")
    assert response.status_code == 403


def test_service_directory_with_unauthorized_principal_returns_403(client):
    response = client.get(
        "/users/service-directory",
        headers={"X-DMS-Principal": "irgendein-principal-ohne-rolle"},
    )
    assert response.status_code == 403


def test_service_directory_does_not_accept_admin_user_management_alone(
    client, domain_admin_auth_headers
):
    """`service.user_lookup` is deliberately its OWN capability, not folded
    into `admin.user_management` - a caller holding the broad domain-admin
    role via a real bearer token (not `X-DMS-Principal`) must still be
    rejected here, proving this endpoint doesn't silently also accept the
    JWT-authenticated path `GET /users` uses."""
    response = client.get("/users/service-directory", headers=domain_admin_auth_headers)
    assert response.status_code == 403


def test_service_directory_finds_user_by_email_and_username(
    client, domain_admin_auth_headers, authorized_service_principal
):
    payload = _user_payload()
    created = client.post("/users", json=payload, headers=domain_admin_auth_headers).json()

    response = client.get(
        "/users/service-directory",
        headers={"X-DMS-Principal": authorized_service_principal},
    )

    assert response.status_code == 200
    entries = response.json()
    match = next(e for e in entries if e["id"] == created["id"])
    assert match["username"] == payload["username"]
    assert match["email"] == payload["email"]
    # Same reduced shape as `GET /users/directory` (`DirectoryEntryOut`) -
    # no `enabled` field, unlike `GET /users`.
    assert "enabled" not in match

    client.delete(f"/users/{created['id']}", headers=domain_admin_auth_headers)
