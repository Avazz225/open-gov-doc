import uuid

import httpx
import pytest
from auth_service.settings import Settings

settings = Settings()

# Same pattern as `test_service_directory.py`'s own
# `SERVICE_DIRECTORY_TEST_PRINCIPAL` (Post-Roadmap Phase 74 Session 3,
# ADR 0160/ADR 0217): a dedicated test principal that individual tests
# grant/withhold `service-group-lookup` for.
GROUP_LOOKUP_TEST_PRINCIPAL = "auth-service-group-lookup-tests"


@pytest.fixture
def keycloak_group(keycloak_admin):
    """Same shape as `test_ad_group_mapping.py`'s own fixture of the same
    name - a fresh, real Keycloak group, `(name, group_id)`, cleaned up
    afterward."""
    name = f"group-members-test-{uuid.uuid4().hex[:8]}"
    group_id = keycloak_admin.create_group(payload={"name": name})
    yield name, group_id
    keycloak_admin.delete_group(group_id)


def _add_user_to_group(keycloak_admin, username: str, group_id: str) -> None:
    user_id = keycloak_admin.get_user_id(username)
    keycloak_admin.group_user_add(user_id, group_id)


@pytest.fixture
def authorized_service_principal(role_assignment_immediate):
    with httpx.Client(base_url=settings.permission_service_base_url, timeout=10.0) as pc:
        roles = pc.get("/roles").json()
        role = next(r for r in roles if r["name"] == "service-group-lookup")
        assignment = pc.post(
            "/role-assignments",
            json={
                "principal_type": "service",
                "principal_id": GROUP_LOOKUP_TEST_PRINCIPAL,
                "role_id": role["id"],
                "resource_id": "root",
            },
        ).json()["role_assignment"]
        yield GROUP_LOOKUP_TEST_PRINCIPAL
        pc.delete(f"/role-assignments/{assignment['id']}")


def test_group_members_without_principal_returns_403(client):
    response = client.get("/groups/some-group/members")
    assert response.status_code == 403


def test_group_members_with_unauthorized_principal_returns_403(client):
    response = client.get(
        "/groups/some-group/members",
        headers={"X-DMS-Principal": "irgendein-principal-ohne-rolle"},
    )
    assert response.status_code == 403


def test_group_members_does_not_accept_admin_user_management_alone(
    client, domain_admin_auth_headers
):
    """`service.group_lookup` is deliberately its OWN capability, not
    folded into `service.user_lookup`/`admin.user_management` - a caller
    holding the broad domain-admin role via a real bearer token (not
    `X-DMS-Principal`) must still be rejected here."""
    response = client.get("/groups/some-group/members", headers=domain_admin_auth_headers)
    assert response.status_code == 403


def test_unknown_group_returns_404(client, authorized_service_principal):
    response = client.get(
        "/groups/does-not-exist-xyz/members",
        headers={"X-DMS-Principal": authorized_service_principal},
    )
    assert response.status_code == 404


def test_group_members_returns_the_real_membership_list(
    client, keycloak_admin, keycloak_group, test_user, authorized_service_principal
):
    name, group_id = keycloak_group
    _add_user_to_group(keycloak_admin, test_user["username"], group_id)

    response = client.get(
        f"/groups/{name}/members",
        headers={"X-DMS-Principal": authorized_service_principal},
    )

    assert response.status_code == 200
    members = response.json()
    assert len(members) == 1
    assert members[0]["username"] == test_user["username"]
    # Deliberately minimal shape, same as `GET /users/lookup` -
    # `UserLookupOut` (`id`/`username` only).
    assert set(members[0].keys()) == {"id", "username"}


def test_empty_group_returns_empty_list(client, keycloak_group, authorized_service_principal):
    name, _group_id = keycloak_group

    response = client.get(
        f"/groups/{name}/members",
        headers={"X-DMS-Principal": authorized_service_principal},
    )

    assert response.status_code == 200
    assert response.json() == []
