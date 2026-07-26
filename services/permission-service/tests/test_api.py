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

    after = client.get(f"/effective-permissions/carol/{ROOT_RESOURCE_ID}").json()
    assert after["permissions"] == []
