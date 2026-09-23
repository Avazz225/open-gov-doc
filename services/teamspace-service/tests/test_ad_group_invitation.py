"""AD-group invitation (2.5, Post-Roadmap Phase 74 Session 3, ADR
0160/ADR 0217) - runs against the real, live `auth-service`/
`permission-service` (no mocking), same convention as `test_api.py`.
Creates real Keycloak groups directly via `python-keycloak`, the same
tool this module's own `AuthServiceClient` wraps on the `auth-service`
side."""

import os
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from keycloak import KeycloakAdmin
from teamspace_service.main import app

FOLDER_SERVICE_URL = os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
KEYCLOAK_BASE_URL = os.environ.get("TEST_KEYCLOAK_BASE_URL", "http://localhost:8080")
KEYCLOAK_REALM = os.environ.get("TEST_KEYCLOAK_REALM", "dms")
KEYCLOAK_ADMIN_USERNAME = os.environ.get("TEST_KEYCLOAK_ADMIN_USERNAME", "admin")
KEYCLOAK_ADMIN_PASSWORD = os.environ.get("TEST_KEYCLOAK_ADMIN_PASSWORD", "admin_dev_only")

# Same orphaned-`inherit=False`-folder cleanup concern as `test_api.py`'s
# own `_created_teamspace_folders`/`_cleanup_teamspace_folders` (ADR
# 0149) - duplicated locally rather than imported across test modules
# (no precedent for that in this codebase), see that file's own docstring
# for the full reasoning.
_created_teamspace_folders: list[tuple[str, str]] = []


def _headers(principal: str) -> dict[str, str]:
    return {"X-DMS-Principal": principal}


def _create_teamspace(client, *, name: str = "AD-Gruppen-Test", principal: str = "alice") -> dict:
    response = client.post(
        "/teamspaces",
        json={"name": name, "description": "Testbeschreibung"},
        headers=_headers(principal),
    )
    assert response.status_code == 201
    body = response.json()
    _created_teamspace_folders.append((body["root_folder_id"], principal))
    return body


@pytest.fixture(autouse=True)
def _cleanup_teamspace_folders():
    _created_teamspace_folders.clear()
    yield
    if not _created_teamspace_folders:
        return
    with httpx.Client(base_url=FOLDER_SERVICE_URL, timeout=30.0) as fc:
        for folder_id, principal in _created_teamspace_folders:
            headers = {"X-DMS-Principal": principal, "X-DMS-Roles": "dms-admin"}
            fc.post(
                f"/folders/{folder_id}/trash",
                json={"deleted_by": principal},
                headers=headers,
            )
            fc.post(f"/folders/{folder_id}/purge", headers=headers)
    _created_teamspace_folders.clear()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def keycloak_admin():
    admin = KeycloakAdmin(
        server_url=KEYCLOAK_BASE_URL,
        username=KEYCLOAK_ADMIN_USERNAME,
        password=KEYCLOAK_ADMIN_PASSWORD,
        realm_name="master",
        user_realm_name="master",
    )
    admin.change_current_realm(KEYCLOAK_REALM)
    return admin


@pytest.fixture
def keycloak_group(keycloak_admin):
    name = f"teamspace-ad-group-test-{uuid.uuid4().hex[:8]}"
    group_id = keycloak_admin.create_group(payload={"name": name})
    yield name, group_id
    keycloak_admin.delete_group(group_id)


def _add_user_to_group(keycloak_admin, username: str, group_id: str) -> None:
    user_id = keycloak_admin.get_user_id(username)
    keycloak_admin.group_user_add(user_id, group_id)


def _remove_user_from_group(keycloak_admin, username: str, group_id: str) -> None:
    user_id = keycloak_admin.get_user_id(username)
    keycloak_admin.group_user_remove(user_id, group_id)


@pytest.fixture
def keycloak_user(keycloak_admin):
    """A minimal real Keycloak user, principal id = the Keycloak `sub`
    UUID - matches what `GET /groups/{name}/members` actually returns
    (`{id, username}`), unlike this project's usual plain-string test
    principals (`alice`/`bob`)."""
    username = f"teamspace-ad-group-test-user-{uuid.uuid4().hex[:8]}"
    user_id = keycloak_admin.create_user(
        payload={
            "username": username,
            "enabled": True,
            "email": f"{username}@example.com",
            "emailVerified": True,
            "firstName": "Test",
            "lastName": "User",
            "credentials": [{"type": "password", "value": "testpass123", "temporary": False}],
        }
    )
    yield username, user_id
    keycloak_admin.delete_user(user_id)


def test_preview_ad_group_requires_manager(client, keycloak_group):
    name, _group_id = keycloak_group
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "carol"},
        headers=_headers("alice"),
    )
    response = client.get(
        f"/teamspaces/{teamspace['id']}/ad-group-preview",
        params={"ad_group_name": name},
        headers=_headers("carol"),
    )
    assert response.status_code == 403


def test_preview_ad_group_returns_real_members(
    client, keycloak_admin, keycloak_group, keycloak_user
):
    name, group_id = keycloak_group
    username, user_id = keycloak_user
    _add_user_to_group(keycloak_admin, username, group_id)
    teamspace = _create_teamspace(client)

    response = client.get(
        f"/teamspaces/{teamspace['id']}/ad-group-preview",
        params={"ad_group_name": name},
        headers=_headers("alice"),
    )

    assert response.status_code == 200
    members = response.json()
    assert len(members) == 1
    assert members[0]["id"] == user_id
    assert members[0]["username"] == username


def test_preview_unknown_ad_group_returns_404(client):
    teamspace = _create_teamspace(client)
    response = client.get(
        f"/teamspaces/{teamspace['id']}/ad-group-preview",
        params={"ad_group_name": "does-not-exist-xyz"},
        headers=_headers("alice"),
    )
    assert response.status_code == 404


def test_bind_ad_group_requires_manager(client, keycloak_group):
    name, _group_id = keycloak_group
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "carol"},
        headers=_headers("alice"),
    )
    response = client.post(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings",
        json={"ad_group_name": name},
        headers=_headers("carol"),
    )
    assert response.status_code == 403


def test_bind_unknown_ad_group_returns_404(client):
    teamspace = _create_teamspace(client)
    response = client.post(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings",
        json={"ad_group_name": "does-not-exist-xyz"},
        headers=_headers("alice"),
    )
    assert response.status_code == 404


def test_bind_ad_group_immediately_syncs_current_members(
    client, keycloak_admin, keycloak_group, keycloak_user
):
    name, group_id = keycloak_group
    username, user_id = keycloak_user
    _add_user_to_group(keycloak_admin, username, group_id)
    teamspace = _create_teamspace(client)

    response = client.post(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings",
        json={"ad_group_name": name},
        headers=_headers("alice"),
    )
    assert response.status_code == 201
    assert response.json()["ad_group_name"] == name

    members_response = client.get(
        f"/teamspaces/{teamspace['id']}/members", headers=_headers("alice")
    )
    members = members_response.json()
    match = next(m for m in members if m["principal_id"] == user_id)
    assert match["source_ad_group_name"] == name
    assert match["can_manage_members"] is False

    with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
        assignments = permission_client.get(
            "/role-assignments",
            params={"principal_id": user_id, "resource_id": teamspace["root_folder_id"]},
        ).json()
    assert len(assignments) == 1


def test_bind_ad_group_twice_returns_409(client, keycloak_group):
    name, _group_id = keycloak_group
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings",
        json={"ad_group_name": name},
        headers=_headers("alice"),
    )
    response = client.post(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings",
        json={"ad_group_name": name},
        headers=_headers("alice"),
    )
    assert response.status_code == 409


def test_delete_teamspace_with_an_active_ad_group_binding_succeeds(client, keycloak_group):
    """Real bug found live during this session's own browser verification
    (a raw `IntegrityError` from Postgres, not just inferred): an active
    `TeamspaceAdGroupBinding` row blocked `DELETE /teamspaces/{id}` via
    its own foreign key, same "delete the dependents first" gap the
    member/appointment/contact tables had already closed before this
    session - `repository.delete_teamspace` was simply missing the fourth
    one."""
    name, _group_id = keycloak_group
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings",
        json={"ad_group_name": name},
        headers=_headers("alice"),
    )

    response = client.delete(f"/teamspaces/{teamspace['id']}", headers=_headers("alice"))

    assert response.status_code == 204


def test_bind_does_not_overwrite_an_existing_manual_membership(
    client, keycloak_admin, keycloak_group, keycloak_user
):
    """A person already manually invited keeps `source_ad_group_name`
    unset (`None`) even after their AD group is bound - the reconciler
    must never claim attribution over a row it didn't create, per
    `TeamspaceMember.source_ad_group_name`'s own docstring."""
    name, group_id = keycloak_group
    username, user_id = keycloak_user
    _add_user_to_group(keycloak_admin, username, group_id)
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": user_id},
        headers=_headers("alice"),
    )

    client.post(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings",
        json={"ad_group_name": name},
        headers=_headers("alice"),
    )

    members = client.get(f"/teamspaces/{teamspace['id']}/members", headers=_headers("alice")).json()
    match = next(m for m in members if m["principal_id"] == user_id)
    assert match["source_ad_group_name"] is None


def test_list_ad_group_bindings(client, keycloak_group):
    name, _group_id = keycloak_group
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings",
        json={"ad_group_name": name},
        headers=_headers("alice"),
    )
    response = client.get(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings", headers=_headers("alice")
    )
    assert response.status_code == 200
    assert [b["ad_group_name"] for b in response.json()] == [name]


def test_unbind_ad_group_revokes_group_sourced_members_but_not_manual_ones(
    client, keycloak_admin, keycloak_group, keycloak_user
):
    name, group_id = keycloak_group
    username, user_id = keycloak_user
    _add_user_to_group(keycloak_admin, username, group_id)
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "manually-invited-carol"},
        headers=_headers("alice"),
    )
    client.post(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings",
        json={"ad_group_name": name},
        headers=_headers("alice"),
    )

    response = client.delete(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings/{name}", headers=_headers("alice")
    )
    assert response.status_code == 204

    members = {
        m["principal_id"]
        for m in client.get(
            f"/teamspaces/{teamspace['id']}/members", headers=_headers("alice")
        ).json()
    }
    assert user_id not in members
    assert "manually-invited-carol" in members
    assert "alice" in members

    bindings = client.get(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings", headers=_headers("alice")
    ).json()
    assert bindings == []


def test_unbind_unknown_binding_returns_404(client):
    teamspace = _create_teamspace(client)
    response = client.delete(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings/does-not-exist-xyz",
        headers=_headers("alice"),
    )
    assert response.status_code == 404


def test_reconciliation_removes_a_member_who_left_the_ad_group(
    client, keycloak_admin, keycloak_group, keycloak_user
):
    """Waits out a real reconciliation poll tick (`conftest.py` sets a 1s
    test-time interval, vs. the 300s production default) rather than
    calling `_reconcile_ad_group_binding` directly - that call runs on
    `TestClient(app)`'s own AnyIO portal event loop for its `httpx`/`asyncpg`
    connections, a different loop than a `pytest-asyncio` test function's
    own, which breaks with 'attached to a different loop'. Proves the
    poll-tick removal half of the reconciliation logic, the part
    `test_bind_ad_group_immediately_syncs_current_members` above doesn't
    cover (that one only exercises the initial, bind-time sync)."""
    name, group_id = keycloak_group
    username, user_id = keycloak_user
    _add_user_to_group(keycloak_admin, username, group_id)
    teamspace = _create_teamspace(client)
    bind_response = client.post(
        f"/teamspaces/{teamspace['id']}/ad-group-bindings",
        json={"ad_group_name": name},
        headers=_headers("alice"),
    )
    assert bind_response.json()["ad_group_name"] == name

    _remove_user_from_group(keycloak_admin, username, group_id)
    time.sleep(3)

    members = {
        m["principal_id"]
        for m in client.get(
            f"/teamspaces/{teamspace['id']}/members", headers=_headers("alice")
        ).json()
    }
    assert user_id not in members
