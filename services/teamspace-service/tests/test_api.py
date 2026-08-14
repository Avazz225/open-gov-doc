"""Läuft wie jeder andere Service dieses Projekts gegen die echten, per
docker-compose laufenden Nachbar-Services (`folder-service`/`permission-service`,
kein Mocking) - `tests/conftest.py` zeigt `DMS_FOLDER_SERVICE_BASE_URL`/
`DMS_PERMISSION_SERVICE_BASE_URL` explizit auf den lokal laufenden Stack."""

import os

import httpx
import pytest
from fastapi.testclient import TestClient
from teamspace_service.main import app

FOLDER_SERVICE_URL = os.environ.get("TEST_FOLDER_SERVICE_URL", "http://localhost:8008")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _headers(principal: str) -> dict[str, str]:
    return {"X-DMS-Principal": principal}


def _create_teamspace(client, *, name: str = "Projekt X", principal: str = "alice") -> dict:
    response = client.post(
        "/teamspaces",
        json={"name": name, "description": "Testbeschreibung"},
        headers=_headers(principal),
    )
    assert response.status_code == 201
    return response.json()


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "teamspace-service"


def test_create_teamspace_without_principal_is_forbidden(client):
    response = client.post("/teamspaces", json={"name": "Projekt X"})
    assert response.status_code == 403


def test_create_teamspace_creates_real_root_folder(client):
    teamspace = _create_teamspace(client)
    assert teamspace["created_by"] == "alice"
    assert teamspace["root_folder_id"]

    with httpx.Client(base_url=FOLDER_SERVICE_URL) as folder_client:
        folder_response = folder_client.get(f"/folders/{teamspace['root_folder_id']}")
    assert folder_response.status_code == 200
    assert folder_response.json()["name"] == teamspace["name"]
    assert folder_response.json()["parent_id"] == "root"


def test_create_teamspace_grants_creator_permission_service_access(client):
    teamspace = _create_teamspace(client)

    with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
        roles = permission_client.get("/roles").json()
        role = next(r for r in roles if r["name"] == "teamspace-member")
        assignments = permission_client.get(
            "/role-assignments",
            params={"principal_id": "alice", "resource_id": teamspace["root_folder_id"]},
        ).json()
    assert any(a["role_id"] == role["id"] for a in assignments)


def test_get_teamspace_as_non_member_is_forbidden(client):
    teamspace = _create_teamspace(client)
    response = client.get(f"/teamspaces/{teamspace['id']}", headers=_headers("mallory"))
    assert response.status_code == 403


def test_get_teamspace_as_member_succeeds(client):
    teamspace = _create_teamspace(client)
    response = client.get(f"/teamspaces/{teamspace['id']}", headers=_headers("alice"))
    assert response.status_code == 200


def test_get_unknown_teamspace_returns_404(client):
    response = client.get("/teamspaces/does-not-exist", headers=_headers("alice"))
    assert response.status_code == 404


def test_list_teamspaces_only_shows_member_of(client):
    _create_teamspace(client, name="Alice-Space", principal="alice")
    _create_teamspace(client, name="Bob-Space", principal="bob")
    response = client.get("/teamspaces", headers=_headers("alice"))
    names = [t["name"] for t in response.json()]
    assert "Alice-Space" in names
    assert "Bob-Space" not in names


def _grant_teamspace_admin_permission(principal: str) -> None:
    """Weist `principal` real gegen den live laufenden `permission-service`
    die vorgeseedete `domain-admin-teamspaces`-Rolle
    (`admin.teamspace_management`) an der Wurzelressource zu - identisches
    Muster wie die übrigen `_grant_*_permission`-Testhelfer in diesem
    Projekt (z. B. `workflow-service`s `conftest.py`)."""
    with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
        roles = permission_client.get("/roles").json()
        role = next(r for r in roles if r["name"] == "domain-admin-teamspaces")
        permission_client.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": principal,
                "role_id": role["id"],
                "resource_id": "root",
            },
        )


def test_list_all_teamspaces_without_principal_is_forbidden(client):
    response = client.get("/admin/teamspaces")
    assert response.status_code == 403


def test_list_all_teamspaces_without_capability_is_forbidden(client):
    response = client.get("/admin/teamspaces", headers=_headers("mallory"))
    assert response.status_code == 403


def test_list_all_teamspaces_shows_every_teamspace_with_member_count(client):
    _grant_teamspace_admin_permission("dana")
    _create_teamspace(client, name="Alice-Admin-Space", principal="alice")
    bob_teamspace = _create_teamspace(client, name="Bob-Admin-Space", principal="bob")
    client.post(
        f"/teamspaces/{bob_teamspace['id']}/members",
        json={"principal_id": "carol"},
        headers=_headers("bob"),
    )

    response = client.get("/admin/teamspaces", headers=_headers("dana"))
    assert response.status_code == 200
    by_name = {t["name"]: t for t in response.json()}

    # "dana" ist selbst Mitglied von KEINEM der beiden Teamspaces - die
    # Übersicht zeigt trotzdem beide, anders als `GET /teamspaces`.
    assert "Alice-Admin-Space" in by_name
    assert "Bob-Admin-Space" in by_name
    assert by_name["Alice-Admin-Space"]["member_count"] == 1
    assert by_name["Bob-Admin-Space"]["member_count"] == 2


def test_invite_member_by_non_manager_is_forbidden(client):
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "carol"},
        headers=_headers("alice"),
    )
    response = client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "dave"},
        headers=_headers("carol"),
    )
    assert response.status_code == 403


def test_invite_member_succeeds_and_grants_permission_access(client):
    teamspace = _create_teamspace(client)
    response = client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )
    assert response.status_code == 201
    assert response.json()["principal_id"] == "bob"

    with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
        assignments = permission_client.get(
            "/role-assignments",
            params={"principal_id": "bob", "resource_id": teamspace["root_folder_id"]},
        ).json()
    assert len(assignments) == 1


def test_invite_member_duplicate_returns_409(client):
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )
    response = client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )
    assert response.status_code == 409


def test_list_members(client):
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )
    response = client.get(f"/teamspaces/{teamspace['id']}/members", headers=_headers("bob"))
    assert response.status_code == 200
    assert {m["principal_id"] for m in response.json()} == {"alice", "bob"}


def test_update_member_requires_manager(client):
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )
    response = client.put(
        f"/teamspaces/{teamspace['id']}/members/bob",
        json={"can_manage_members": True},
        headers=_headers("bob"),
    )
    assert response.status_code == 403


def test_update_member_by_manager_succeeds(client):
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )
    response = client.put(
        f"/teamspaces/{teamspace['id']}/members/bob",
        json={"can_manage_members": True},
        headers=_headers("alice"),
    )
    assert response.status_code == 200
    assert response.json()["can_manage_members"] is True


def test_remove_self_is_allowed_without_manager_capability(client):
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )
    response = client.delete(f"/teamspaces/{teamspace['id']}/members/bob", headers=_headers("bob"))
    assert response.status_code == 204


def test_remove_other_member_requires_manager(client):
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "carol"},
        headers=_headers("alice"),
    )
    response = client.delete(
        f"/teamspaces/{teamspace['id']}/members/carol", headers=_headers("bob")
    )
    assert response.status_code == 403


def test_remove_member_revokes_permission_service_access(client):
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )
    response = client.delete(
        f"/teamspaces/{teamspace['id']}/members/bob", headers=_headers("alice")
    )
    assert response.status_code == 204

    with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
        assignments = permission_client.get(
            "/role-assignments",
            params={"principal_id": "bob", "resource_id": teamspace["root_folder_id"]},
        ).json()
    assert assignments == []


def test_delete_teamspace_requires_manager(client):
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )
    response = client.delete(f"/teamspaces/{teamspace['id']}", headers=_headers("bob"))
    assert response.status_code == 403


def test_delete_teamspace_succeeds(client):
    teamspace = _create_teamspace(client)
    response = client.delete(f"/teamspaces/{teamspace['id']}", headers=_headers("alice"))
    assert response.status_code == 204
    assert (
        client.get(f"/teamspaces/{teamspace['id']}", headers=_headers("alice")).status_code == 404
    )


def test_create_and_list_appointments(client):
    teamspace = _create_teamspace(client)
    response = client.post(
        f"/teamspaces/{teamspace['id']}/appointments",
        json={
            "title": "Kickoff",
            "description": "Erstes Treffen",
            "start_at": "2026-03-01T10:00:00Z",
            "end_at": "2026-03-01T11:00:00Z",
        },
        headers=_headers("alice"),
    )
    assert response.status_code == 201
    list_response = client.get(
        f"/teamspaces/{teamspace['id']}/appointments", headers=_headers("alice")
    )
    assert len(list_response.json()) == 1
    assert list_response.json()[0]["title"] == "Kickoff"


def test_delete_appointment(client):
    teamspace = _create_teamspace(client)
    created = client.post(
        f"/teamspaces/{teamspace['id']}/appointments",
        json={
            "title": "Kickoff",
            "start_at": "2026-03-01T10:00:00Z",
            "end_at": "2026-03-01T11:00:00Z",
        },
        headers=_headers("alice"),
    ).json()
    response = client.delete(
        f"/teamspaces/{teamspace['id']}/appointments/{created['id']}", headers=_headers("alice")
    )
    assert response.status_code == 204
    assert (
        client.get(f"/teamspaces/{teamspace['id']}/appointments", headers=_headers("alice")).json()
        == []
    )


def test_create_and_list_contacts(client):
    teamspace = _create_teamspace(client)
    response = client.post(
        f"/teamspaces/{teamspace['id']}/contacts",
        json={"name": "Anna Beispiel", "email": "anna@example.com"},
        headers=_headers("alice"),
    )
    assert response.status_code == 201
    list_response = client.get(f"/teamspaces/{teamspace['id']}/contacts", headers=_headers("alice"))
    assert len(list_response.json()) == 1
    assert list_response.json()[0]["name"] == "Anna Beispiel"


def test_delete_contact(client):
    teamspace = _create_teamspace(client)
    created = client.post(
        f"/teamspaces/{teamspace['id']}/contacts",
        json={"name": "Anna Beispiel"},
        headers=_headers("alice"),
    ).json()
    response = client.delete(
        f"/teamspaces/{teamspace['id']}/contacts/{created['id']}", headers=_headers("alice")
    )
    assert response.status_code == 204
    assert (
        client.get(f"/teamspaces/{teamspace['id']}/contacts", headers=_headers("alice")).json()
        == []
    )


def test_appointments_require_membership(client):
    teamspace = _create_teamspace(client)
    response = client.get(
        f"/teamspaces/{teamspace['id']}/appointments", headers=_headers("mallory")
    )
    assert response.status_code == 403
