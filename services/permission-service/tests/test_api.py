import pytest
from fastapi.testclient import TestClient
from permission_service.main import app
from permission_service.settings import ROOT_RESOURCE_ID


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "permission-service"


def test_create_role(client):
    response = client.post(
        "/roles", json={"name": "Viewer", "description": "", "permissions": ["read"]}
    )
    assert response.status_code == 200
    assert response.json()["permissions"] == ["read"]


def test_assignment_with_unknown_role_returns_404(client):
    response = client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "alice",
            "role_id": 999999,
            "resource_id": ROOT_RESOURCE_ID,
        },
    )
    assert response.status_code == 404


def test_assignment_with_unknown_resource_returns_404(client):
    role = client.post("/roles", json={"name": "Viewer2", "permissions": ["read"]}).json()
    response = client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "alice",
            "role_id": role["id"],
            "resource_id": "does-not-exist",
        },
    )
    assert response.status_code == 404


def test_full_flow_via_api(client):
    role = client.post("/roles", json={"name": "Editor", "permissions": ["read", "write"]}).json()
    assignment = client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "carol",
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    ).json()

    effective = client.get(f"/effective-permissions/carol/{ROOT_RESOURCE_ID}").json()
    assert set(effective["permissions"]) == {"read", "write"}

    allowed = client.get(
        "/check",
        params={"principal_id": "carol", "resource_id": ROOT_RESOURCE_ID, "permission": "write"},
    ).json()
    assert allowed["allowed"] is True

    denied = client.get(
        "/check",
        params={"principal_id": "carol", "resource_id": ROOT_RESOURCE_ID, "permission": "delete"},
    ).json()
    assert denied["allowed"] is False

    delete_response = client.delete(f"/role-assignments/{assignment['id']}")
    assert delete_response.status_code == 204


def test_list_role_assignments_returns_all(client):
    role = client.post("/roles", json={"name": "Listener", "permissions": ["read"]}).json()
    created = client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "dave",
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    ).json()

    response = client.get("/role-assignments")

    assert response.status_code == 200
    ids = [a["id"] for a in response.json()]
    assert created["id"] in ids


def test_list_role_assignments_filters_by_principal_id(client):
    role = client.post("/roles", json={"name": "Filterable", "permissions": ["read"]}).json()
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "erin",
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    )
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "frank",
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    )

    response = client.get("/role-assignments", params={"principal_id": "erin"})

    assert response.status_code == 200
    principal_ids = {a["principal_id"] for a in response.json()}
    assert principal_ids == {"erin"}

    after = client.get(f"/effective-permissions/carol/{ROOT_RESOURCE_ID}").json()
    assert after["permissions"] == []


def test_scope_lock_with_unknown_resource_returns_404(client):
    response = client.post(
        "/scope-locks",
        json={"resource_id": "does-not-exist", "locked_by": "admin"},
    )
    assert response.status_code == 404


def test_scope_lock_blocks_write_check_with_reason(client):
    lock = client.post(
        "/scope-locks",
        json={
            "resource_id": ROOT_RESOURCE_ID,
            "locked_by": "admin",
            "reason": "Revision läuft",
        },
    ).json()

    result = client.get(
        "/check",
        params={
            "principal_id": "dave",
            "resource_id": ROOT_RESOURCE_ID,
            "permission": "write",
            "access_type": "write",
        },
    ).json()

    assert result["allowed"] is False
    assert result["blocked_by_scope_lock"] is True
    assert result["scope_lock_reason"] == "Revision läuft"

    release = client.request("DELETE", f"/scope-locks/{lock['id']}", json={"released_by": "admin"})
    assert release.status_code == 200
    assert release.json()["released_by"] == "admin"

    after_release = client.get(
        "/check",
        params={
            "principal_id": "dave",
            "resource_id": ROOT_RESOURCE_ID,
            "permission": "write",
            "access_type": "write",
        },
    ).json()
    assert after_release["blocked_by_scope_lock"] is False


def test_scope_lock_without_blocks_read_allows_read_access(client):
    client.post("/scope-locks", json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"})

    result = client.get(
        "/check",
        params={
            "principal_id": "erin",
            "resource_id": ROOT_RESOURCE_ID,
            "permission": "read",
            "access_type": "read",
        },
    ).json()

    assert result["blocked_by_scope_lock"] is False


def test_scope_lock_with_blocks_read_also_blocks_read_access(client):
    client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin", "blocks_read": True},
    )

    result = client.get(
        "/check",
        params={
            "principal_id": "erin",
            "resource_id": ROOT_RESOURCE_ID,
            "permission": "read",
            "access_type": "read",
        },
    ).json()

    assert result["blocked_by_scope_lock"] is True


def test_scope_lock_bypass_capability_overrides_block(client):
    client.post("/scope-locks", json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"})
    role = client.post(
        "/roles", json={"name": "ScopeLockBypasser", "permissions": ["scope_lock.bypass", "write"]}
    ).json()
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "frank",
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    )

    result = client.get(
        "/check",
        params={
            "principal_id": "frank",
            "resource_id": ROOT_RESOURCE_ID,
            "permission": "write",
            "access_type": "write",
        },
    ).json()

    assert result["blocked_by_scope_lock"] is False
    assert result["allowed"] is True


def test_list_scope_locks_endpoint(client):
    client.post("/scope-locks", json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"})

    response = client.get("/scope-locks", params={"resource_id": ROOT_RESOURCE_ID})

    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_effective_scope_locks_endpoint(client):
    client.post("/scope-locks", json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"})

    response = client.get(f"/scope-locks/effective/{ROOT_RESOURCE_ID}")

    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_release_unknown_scope_lock_returns_404(client):
    response = client.request("DELETE", "/scope-locks/999999", json={"released_by": "admin"})
    assert response.status_code == 404
