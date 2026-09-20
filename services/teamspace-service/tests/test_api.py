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
DOCUMENT_SERVICE_URL = os.environ.get("TEST_DOCUMENT_SERVICE_URL", "http://localhost:8006")
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# Post-Roadmap Phase 38 Session 4 (ADR 0149): `create_teamspace` now also
# registers a REAL resource node (`inherit=False`) in the REAL, live-
# running `permission-service`/`folder-service` (this test file
# deliberately runs against the real neighbor services, see module
# docstring - no mocking). Without cleanup, every test run leaves behind a
# folder under the live installation's real `root` that ONLY its creator
# can ever reach again (that's the whole point of `inherit=False`) - this
# was harmless clutter before this session (no real permission check
# existed to make an orphaned `inherit=False` folder actually
# inaccessible), but now genuinely orphans it, since the corresponding
# `Teamspace` row only ever lived in the test DB, never in a form that
# would let anyone find and clean up the folder later. `_create_teamspace`
# records `(root_folder_id, principal)` so the autouse fixture below can
# purge it afterward using a principal that actually still has a grant
# there (a separate cleanup principal would itself be blocked by the same
# `inherit=False`).
_created_teamspace_folders: list[tuple[str, str]] = []


def _headers(principal: str) -> dict[str, str]:
    return {"X-DMS-Principal": principal}


def _create_teamspace(client, *, name: str = "Projekt X", principal: str = "alice") -> dict:
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

    # Post-Roadmap Phase 38 Session 4 (ADR 0149): `GET /folders/{id}` now
    # requires a valid principal - "alice" is the teamspace creator/member
    # here (real `teamspace-member` grant on exactly this resource, see
    # `test_create_teamspace_grants_creator_permission_service_access`
    # below), so this is the correct principal to verify access with.
    with httpx.Client(base_url=FOLDER_SERVICE_URL) as folder_client:
        folder_response = folder_client.get(
            f"/folders/{teamspace['root_folder_id']}", headers={"X-DMS-Principal": "alice"}
        )
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


def test_non_member_cannot_access_teamspace_folder_via_folder_service_directly(client):
    """Post-Roadmap Phase 38 Session 4 (ADR 0149) - the actual core deliverable
    of this session: teamspace membership must anchor into `permission-
    service`'s resource-tree RBAC such that DIRECT `folder-service`/
    `document-service` access (bypassing `teamspace-service` entirely) is
    also gated by membership, not just `teamspace-service`'s own endpoints
    (already covered by `test_get_teamspace_as_non_member_is_forbidden`
    above) or `search-service` (the only pre-existing real consumer)."""
    teamspace = _create_teamspace(client)
    root_folder_id = teamspace["root_folder_id"]

    with httpx.Client(base_url=FOLDER_SERVICE_URL, timeout=30.0) as folder_client:
        non_member_response = folder_client.get(
            f"/folders/{root_folder_id}", headers={"X-DMS-Principal": "mallory"}
        )
        assert non_member_response.status_code == 403

        member_response = folder_client.get(
            f"/folders/{root_folder_id}", headers={"X-DMS-Principal": "alice"}
        )
        assert member_response.status_code == 200
        assert member_response.json()["id"] == root_folder_id


def test_non_member_cannot_list_teamspace_documents_via_document_service_directly(client):
    """Same as above, one level down: a document actually placed inside the
    teamspace folder must also be unreachable for a non-member via
    `document-service`'s own primary paths, not just folder-listing."""
    teamspace = _create_teamspace(client)
    root_folder_id = teamspace["root_folder_id"]

    with httpx.Client(base_url=DOCUMENT_SERVICE_URL, timeout=30.0) as document_client:
        created = document_client.post(
            "/documents",
            data={
                "title": "Teamspace-Dokument",
                "created_by": "alice",
                "folder_id": root_folder_id,
            },
            files={"file": ("teamspace.txt", b"Inhalt", "text/plain")},
            headers={"X-DMS-Principal": "alice"},
        )
        assert created.status_code == 201
        document_id = created.json()["id"]

        non_member_response = document_client.get(
            f"/documents/{document_id}", headers={"X-DMS-Principal": "mallory"}
        )
        assert non_member_response.status_code == 403

        non_member_list_response = document_client.get(
            "/documents",
            params={"folder_id": root_folder_id},
            headers={"X-DMS-Principal": "mallory"},
        )
        assert non_member_list_response.status_code == 403

        member_response = document_client.get(
            f"/documents/{document_id}", headers={"X-DMS-Principal": "alice"}
        )
        assert member_response.status_code == 200


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


def test_delete_principal_memberships_requires_auth_service_caller(client):
    """P55-S2: `DELETE /principals/{id}/teamspace-memberships` is a
    system-to-system cleanup callback, meant only for `auth-service`'s own
    `DELETE /users/{id}` to invoke."""
    response = client.delete("/principals/does-not-matter/teamspace-memberships")
    assert response.status_code == 403

    response = client.delete(
        "/principals/does-not-matter/teamspace-memberships",
        headers={"X-DMS-Principal": "someone-else"},
    )
    assert response.status_code == 403


def test_delete_principal_memberships_removes_membership_and_revokes_access(client):
    """P55-S2: removes the principal from every teamspace they belong to
    (not the teamspaces themselves, unlike P55-S1/ADR 0175's orphan
    cleanup) and revokes their `permission-service` access on each."""
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )

    response = client.delete(
        "/principals/bob/teamspace-memberships", headers={"X-DMS-Principal": "auth-service"}
    )
    assert response.status_code == 204

    members = client.get(f"/teamspaces/{teamspace['id']}/members", headers=_headers("alice")).json()
    assert "bob" not in {m["principal_id"] for m in members}

    with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
        assignments = permission_client.get(
            "/role-assignments",
            params={"principal_id": "bob", "resource_id": teamspace["root_folder_id"]},
        ).json()
    assert assignments == []

    # The teamspace itself is untouched - alice's own membership survives.
    response = client.get(f"/teamspaces/{teamspace['id']}", headers=_headers("alice"))
    assert response.status_code == 200


def test_delete_principal_memberships_for_a_principal_with_none_is_a_no_op(client):
    response = client.delete(
        "/principals/nobody-is-a-member-anywhere/teamspace-memberships",
        headers={"X-DMS-Principal": "auth-service"},
    )
    assert response.status_code == 204


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


def test_non_manager_member_cannot_delete_or_trash_teamspace_root_folder_via_folder_service(
    client,
):
    """Post-Roadmap Phase 44 Session 2 (ADR 0163) - the actual bypass this
    session closes: before this fix, `teamspace-member`'s `folder.write`
    let ANY member (not just a manager) delete/trash the entire teamspace
    by calling `folder-service` directly, bypassing this service's own
    manager-only guard on `DELETE /teamspaces/{id}` (which only ever
    protected the teamspace METADATA row, never the underlying folder).
    "bob" is invited WITHOUT `can_manage_members` (the default) - a
    completely ordinary member."""
    teamspace = _create_teamspace(client)
    root_folder_id = teamspace["root_folder_id"]
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )

    with httpx.Client(base_url=FOLDER_SERVICE_URL, timeout=30.0) as folder_client:
        trash_response = folder_client.post(
            f"/folders/{root_folder_id}/trash",
            json={"deleted_by": "bob"},
            headers={"X-DMS-Principal": "bob"},
        )
        assert trash_response.status_code == 403

        delete_response = folder_client.delete(
            f"/folders/{root_folder_id}", headers={"X-DMS-Principal": "bob"}
        )
        assert delete_response.status_code == 403


def test_manager_member_can_trash_teamspace_root_folder_via_folder_service(client):
    """Counterpart to the test above - a member invited WITH
    `can_manage_members=True` gets the second, `folder.delete`-carrying
    role (`teamspace-manager`, ADR 0163) and can legitimately trash the
    teamspace root folder directly. Restores it again afterward so the
    module's `_cleanup_teamspace_folders` fixture (trash-then-purge) still
    finds an untrashed folder to work with, same as every other test."""
    teamspace = _create_teamspace(client)
    root_folder_id = teamspace["root_folder_id"]
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "carol", "can_manage_members": True},
        headers=_headers("alice"),
    )

    with httpx.Client(base_url=FOLDER_SERVICE_URL, timeout=30.0) as folder_client:
        trash_response = folder_client.post(
            f"/folders/{root_folder_id}/trash",
            json={"deleted_by": "carol"},
            headers={"X-DMS-Principal": "carol"},
        )
        assert trash_response.status_code == 200

        restore_response = folder_client.post(
            f"/folders/{root_folder_id}/restore", headers={"X-DMS-Principal": "carol"}
        )
        assert restore_response.status_code == 200


def test_promoting_member_grants_folder_delete_and_demoting_revokes_it(client):
    """`update_member` (`PUT .../members/{id}`) must keep the
    `teamspace-manager` role in sync with `can_manage_members` (ADR 0163) -
    not just `invite_member`/`create_teamspace`, which only cover the
    grant at creation/invite time."""
    teamspace = _create_teamspace(client)
    root_folder_id = teamspace["root_folder_id"]
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob"},
        headers=_headers("alice"),
    )

    with httpx.Client(base_url=FOLDER_SERVICE_URL, timeout=30.0) as folder_client:
        assert (
            folder_client.post(
                f"/folders/{root_folder_id}/trash",
                json={"deleted_by": "bob"},
                headers={"X-DMS-Principal": "bob"},
            ).status_code
            == 403
        )

        promote = client.put(
            f"/teamspaces/{teamspace['id']}/members/bob",
            json={"can_manage_members": True},
            headers=_headers("alice"),
        )
        assert promote.status_code == 200

        trashed = folder_client.post(
            f"/folders/{root_folder_id}/trash",
            json={"deleted_by": "bob"},
            headers={"X-DMS-Principal": "bob"},
        )
        assert trashed.status_code == 200
        assert (
            folder_client.post(
                f"/folders/{root_folder_id}/restore", headers={"X-DMS-Principal": "bob"}
            ).status_code
            == 200
        )

        demote = client.put(
            f"/teamspaces/{teamspace['id']}/members/bob",
            json={"can_manage_members": False},
            headers=_headers("alice"),
        )
        assert demote.status_code == 200

        assert (
            folder_client.post(
                f"/folders/{root_folder_id}/trash",
                json={"deleted_by": "bob"},
                headers={"X-DMS-Principal": "bob"},
            ).status_code
            == 403
        )


def test_remove_member_revokes_manager_role_too(client):
    teamspace = _create_teamspace(client)
    client.post(
        f"/teamspaces/{teamspace['id']}/members",
        json={"principal_id": "bob", "can_manage_members": True},
        headers=_headers("alice"),
    )
    response = client.delete(
        f"/teamspaces/{teamspace['id']}/members/bob", headers=_headers("alice")
    )
    assert response.status_code == 204

    with httpx.Client(base_url=PERMISSION_SERVICE_URL) as permission_client:
        roles = permission_client.get("/roles").json()
        manager_role = next(r for r in roles if r["name"] == "teamspace-manager")
        assignments = permission_client.get(
            "/role-assignments",
            params={"principal_id": "bob", "resource_id": teamspace["root_folder_id"]},
        ).json()
    assert not any(a["role_id"] == manager_role["id"] for a in assignments)


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
