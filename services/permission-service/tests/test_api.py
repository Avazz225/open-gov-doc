import uuid
from datetime import UTC, datetime, timedelta

import pytest
from dms_eventbus_client import Event
from fastapi.testclient import TestClient
from permission_service import repository
from permission_service.main import app
from permission_service.settings import ROOT_RESOURCE_ID


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def role_management_headers(client) -> dict[str, str]:
    """Self-Gating (Post-Roadmap Phase 19 Session 6, ADR 0071): `POST`/
    `PUT /roles` und `POST`/`DELETE /scope-locks` verlangen seither
    `admin.user_management`. Weist die bereits vorgeseedete
    "domain-admin-users"-Rolle einem festen Testprincipal ("admin", bereits
    der in den meisten Scope-Lock-Tests verwendete `locked_by`/`released_by`-
    Wert) über den weiterhin ungegateten `/role-assignments`-Endpunkt zu
    (kein Henne-Ei-Problem dort, siehe ADR 0071 "Begründung"). Tests, die nur
    `locked_by`/`released_by="admin"` verwenden, brauchen diese Fixture
    lediglich als Parameter (Ausführung vor dem Testkörper genügt) - der
    zurückgegebene Header wird nur für `POST/PUT /roles`-Aufrufe gebraucht,
    die kein Akteur-Feld im Body haben."""
    roles = client.get("/roles").json()
    role_id = next(r["id"] for r in roles if r["name"] == "domain-admin-users")
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "admin",
            "role_id": role_id,
            "resource_id": ROOT_RESOURCE_ID,
        },
    )
    return {"X-DMS-Principal": "admin"}


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "permission-service"


def test_create_role_requires_authentication(client):
    response = client.post(
        "/roles", json={"name": "Viewer", "description": "", "permissions": ["read"]}
    )
    assert response.status_code == 401


def test_create_role_returns_403_without_permission(client):
    response = client.post(
        "/roles",
        json={"name": "Viewer", "description": "", "permissions": ["read"]},
        headers={"X-DMS-Principal": "nobody"},
    )
    assert response.status_code == 403


def test_create_role_bypassed_by_active_superuser_without_explicit_role(client, monkeypatch):
    """P63-S1: `require_capability`'s six direct call sites previously had
    NO superuser bypass at all (only `POST /maintenance-mode/lift` special-
    cased it, ad hoc) - an activated break-glass superuser genuinely could
    not manage roles unless they ALSO held an explicit `admin.user_
    management` role assignment. Boundary-patches `app.state.auth_client`'s
    `get_active_superuser` (a full real activation would need a genuine
    Keycloak superuser login flow, orthogonal to what this test verifies -
    `require_capability`'s own bypass logic, not auth-service's activation
    mechanism) - same "patch the client, not the mechanism it wraps"
    convention already used elsewhere in this project (e.g. workflow-
    service's XDOMEA handoff tests)."""

    async def fake_get_active_superuser():
        return True, "root-admin"

    monkeypatch.setattr(app.state.auth_client, "get_active_superuser", fake_get_active_superuser)

    response = client.post(
        "/roles",
        json={"name": "SuperuserBypassRole", "description": "", "permissions": ["read"]},
        headers={"X-DMS-Principal": "root-admin"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "created"


def test_create_role_not_bypassed_by_a_different_principal_than_the_active_superuser(
    client, monkeypatch
):
    """The bypass must check that the SPECIFIC caller is the currently
    active superuser, not merely that some superuser is active somewhere -
    same "actor must match" check `lift_maintenance_mode` already had."""

    async def fake_get_active_superuser():
        return True, "root-admin"

    monkeypatch.setattr(app.state.auth_client, "get_active_superuser", fake_get_active_superuser)

    response = client.post(
        "/roles",
        json={"name": "NotTheSuperuserRole", "description": "", "permissions": ["read"]},
        headers={"X-DMS-Principal": "someone-else"},
    )
    assert response.status_code == 403


def test_create_role(client, role_management_headers):
    """Antwort-Envelope seit P32-S1 (ADR 0130) - `status`/`role`, analog zu
    `RoleAssignmentActionResult` seit P17-S3, immer gesetzt unabhängig von
    aktivierter Vier-Augen-Pflicht."""
    response = client.post(
        "/roles",
        json={"name": "Viewer", "description": "", "permissions": ["read"]},
        headers=role_management_headers,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "created"
    assert body["role"]["permissions"] == ["read"]
    assert body["approval_request_id"] is None


def test_create_role_publishes_event(client, role_management_headers, monkeypatch):
    """P71-S2: the direct/default path (no `requires_approval` configured)
    never published anything before this - only the four-eyes-approved
    execution path (`approval_consumer.py`) did, a real forensic-trace
    coverage gap since installations overwhelmingly use the default path."""
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.publisher, "publish", fake_publish)

    response = client.post(
        "/roles",
        json={"name": "Auditor", "description": "", "permissions": ["read"]},
        headers=role_management_headers,
    )

    assert response.status_code == 201
    events = [e for e in published if e.event_type == "permission.role.created"]
    assert len(events) == 1
    assert events[0].payload["name"] == "Auditor"
    assert events[0].actor == role_management_headers["X-DMS-Principal"]


def test_update_role_publishes_event(client, role_management_headers, monkeypatch):
    role_id = client.post(
        "/roles",
        json={"name": "Editor", "description": "", "permissions": ["read"]},
        headers=role_management_headers,
    ).json()["role"]["id"]

    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.publisher, "publish", fake_publish)

    response = client.put(
        f"/roles/{role_id}",
        json={"description": "updated", "permissions": ["read", "write"]},
        headers=role_management_headers,
    )

    assert response.status_code == 200
    events = [e for e in published if e.event_type == "permission.role.updated"]
    assert len(events) == 1
    assert events[0].payload["permissions"] == ["read", "write"]
    assert events[0].actor == role_management_headers["X-DMS-Principal"]


def test_create_role_with_approval_required_defers_creation(client, role_management_headers):
    """Vier-Augen-Retrofit für Rollenanlage (P32-S1, ADR 0130) - identisches
    Muster wie `test_create_role_assignment_with_approval_required_defers_
    creation`, nur mit `permission.role.create`."""
    client.put(
        "/approval-config/permission.role.create",
        json={"requires_approval": True},
        headers=role_management_headers,
    )

    response = client.post(
        "/roles",
        json={"name": "GatedNewRole", "permissions": ["read"]},
        headers=role_management_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["approval_request_id"] is not None
    assert body["role"] is None

    # Die Rolle existiert noch nicht - Genehmigung erfolgt asynchron ueber
    # das Event (siehe test_approval_consumer.py fuer die Konsumentenlogik).
    roles = client.get("/roles").json()
    assert "GatedNewRole" not in [r["name"] for r in roles]


def test_update_role_changes_description_and_permissions(client, role_management_headers):
    """Antwort-Envelope seit P39-S2 (ADR 0151) - `status`/`role`, analog zu
    `test_create_role`, immer gesetzt unabhaengig von aktivierter
    Vier-Augen-Pflicht."""
    created = client.post(
        "/roles",
        json={"name": "Editor", "description": "alt", "permissions": ["read"]},
        headers=role_management_headers,
    ).json()["role"]

    response = client.put(
        f"/roles/{created['id']}",
        json={"description": "neu", "permissions": ["read", "write"]},
        headers=role_management_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "updated"
    assert body["approval_request_id"] is None
    assert body["role"]["name"] == "Editor"
    assert body["role"]["description"] == "neu"
    assert body["role"]["permissions"] == ["read", "write"]


def test_update_unknown_role_returns_404(client, role_management_headers):
    response = client.put(
        "/roles/999999",
        json={"description": "x", "permissions": []},
        headers=role_management_headers,
    )
    assert response.status_code == 404


def test_update_role_with_approval_required_defers_update(client, role_management_headers):
    """Vier-Augen-Retrofit fuer Rollen-Update (P39-S2, ADR 0151) - identisches
    Muster wie `test_create_role_with_approval_required_defers_creation`, nur
    mit `permission.role.update`."""
    created = client.post(
        "/roles",
        json={"name": "GatedEditor", "description": "alt", "permissions": ["read"]},
        headers=role_management_headers,
    ).json()["role"]

    client.put(
        "/approval-config/permission.role.update",
        json={"requires_approval": True},
        headers=role_management_headers,
    )

    response = client.put(
        f"/roles/{created['id']}",
        json={"description": "neu", "permissions": ["read", "write"]},
        headers=role_management_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["approval_request_id"] is not None
    assert body["role"] is None

    # Die Aenderung ist noch nicht angewendet - Genehmigung erfolgt asynchron
    # ueber das Event (siehe test_approval_consumer.py fuer die
    # Konsumentenlogik).
    roles = client.get("/roles").json()
    unchanged = next(r for r in roles if r["id"] == created["id"])
    assert unchanged["description"] == "alt"
    assert unchanged["permissions"] == ["read"]


def test_create_group_requires_authentication(client):
    response = client.post("/groups", json={"name": "Reviewers", "description": ""})
    assert response.status_code == 401


def test_create_group_returns_403_without_permission(client):
    response = client.post(
        "/groups",
        json={"name": "Reviewers", "description": ""},
        headers={"X-DMS-Principal": "nobody"},
    )
    assert response.status_code == 403


def test_create_and_list_groups(client, role_management_headers):
    created = client.post(
        "/groups",
        json={"name": "Reviewers", "description": "Vier-Augen-Prüfer"},
        headers=role_management_headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["name"] == "Reviewers"
    assert body["description"] == "Vier-Augen-Prüfer"

    groups = client.get("/groups").json()
    assert any(g["id"] == body["id"] for g in groups)


def test_delete_unknown_group_returns_404(client, role_management_headers):
    response = client.delete("/groups/does-not-exist", headers=role_management_headers)
    assert response.status_code == 404


def test_add_member_to_unknown_group_returns_404(client, role_management_headers):
    response = client.post(
        "/groups/does-not-exist/members",
        json={"principal_id": "carol"},
        headers=role_management_headers,
    )
    assert response.status_code == 404


def test_remove_unknown_member_returns_404(client, role_management_headers):
    group = client.post(
        "/groups", json={"name": "Reviewers2"}, headers=role_management_headers
    ).json()
    response = client.delete(
        f"/groups/{group['id']}/members/carol", headers=role_management_headers
    )
    assert response.status_code == 404


def test_add_member_is_idempotent(client, role_management_headers):
    group = client.post(
        "/groups", json={"name": "Reviewers3"}, headers=role_management_headers
    ).json()
    first = client.post(
        f"/groups/{group['id']}/members",
        json={"principal_id": "carol"},
        headers=role_management_headers,
    ).json()
    second = client.post(
        f"/groups/{group['id']}/members",
        json={"principal_id": "carol"},
        headers=role_management_headers,
    ).json()
    assert first["id"] == second["id"]

    members = client.get(f"/groups/{group['id']}/members").json()
    assert [m["principal_id"] for m in members] == ["carol"]


def test_group_membership_grants_role_to_every_member(client, role_management_headers):
    """Kernverhalten (Post-Roadmap Phase 22 Session 2): eine Rollenzuweisung
    an eine Gruppe (statt an einen einzelnen Principal) gilt für jedes
    eingetragene Mitglied - hier zwei verschiedene Principals, nur EINE
    Rollenzuweisung."""
    group = client.post(
        "/groups", json={"name": "Reviewers4"}, headers=role_management_headers
    ).json()
    client.post(
        f"/groups/{group['id']}/members",
        json={"principal_id": "dave"},
        headers=role_management_headers,
    )
    client.post(
        f"/groups/{group['id']}/members",
        json={"principal_id": "erin"},
        headers=role_management_headers,
    )
    role = client.post(
        "/roles",
        json={"name": "GroupEditor", "permissions": ["group_read"]},
        headers=role_management_headers,
    ).json()["role"]
    client.post(
        "/role-assignments",
        json={
            "principal_type": "group",
            "principal_id": group["id"],
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    )

    for principal_id in ("dave", "erin"):
        allowed = client.get(
            "/check",
            params={
                "principal_id": principal_id,
                "resource_id": ROOT_RESOURCE_ID,
                "permission": "group_read",
            },
        ).json()
        assert allowed["allowed"] is True

    # Ein Nicht-Mitglied bleibt unbetroffen.
    denied = client.get(
        "/check",
        params={
            "principal_id": "frank",
            "resource_id": ROOT_RESOURCE_ID,
            "permission": "group_read",
        },
    ).json()
    assert denied["allowed"] is False

    # Entfernen eines Mitglieds entzieht die Berechtigung wieder (Cache-
    # Invalidierung), das verbleibende Mitglied behält sie.
    remove_response = client.delete(
        f"/groups/{group['id']}/members/dave", headers=role_management_headers
    )
    assert remove_response.status_code == 204
    dave_after = client.get(
        "/check",
        params={
            "principal_id": "dave",
            "resource_id": ROOT_RESOURCE_ID,
            "permission": "group_read",
        },
    ).json()
    assert dave_after["allowed"] is False
    erin_after = client.get(
        "/check",
        params={
            "principal_id": "erin",
            "resource_id": ROOT_RESOURCE_ID,
            "permission": "group_read",
        },
    ).json()
    assert erin_after["allowed"] is True


def test_delete_group_removes_it_and_its_memberships(client, role_management_headers):
    group = client.post(
        "/groups", json={"name": "Reviewers5"}, headers=role_management_headers
    ).json()
    client.post(
        f"/groups/{group['id']}/members",
        json={"principal_id": "gina"},
        headers=role_management_headers,
    )

    response = client.delete(f"/groups/{group['id']}", headers=role_management_headers)
    assert response.status_code == 204

    groups = client.get("/groups").json()
    assert all(g["id"] != group["id"] for g in groups)


def test_create_supervisor_assignment_requires_authentication(client):
    response = client.post(
        "/supervisor-assignments",
        json={"principal_id": "alice", "supervisor_principal_id": "bob"},
    )
    assert response.status_code == 401


def test_create_supervisor_assignment_returns_403_without_permission(client):
    response = client.post(
        "/supervisor-assignments",
        json={"principal_id": "alice", "supervisor_principal_id": "bob"},
        headers={"X-DMS-Principal": "nobody"},
    )
    assert response.status_code == 403


def test_create_and_list_supervisor_assignments(client, role_management_headers):
    created = client.post(
        "/supervisor-assignments",
        json={"principal_id": "alice-s9", "supervisor_principal_id": "bob-s9"},
        headers=role_management_headers,
    )
    assert created.status_code == 201
    body = created.json()
    assert body["principal_id"] == "alice-s9"
    assert body["supervisor_principal_id"] == "bob-s9"

    direct_supervisors = client.get(
        "/supervisor-assignments", params={"principal_id": "alice-s9"}
    ).json()
    assert [a["supervisor_principal_id"] for a in direct_supervisors] == ["bob-s9"]

    direct_reports = client.get(
        "/supervisor-assignments", params={"supervisor_principal_id": "bob-s9"}
    ).json()
    assert [a["principal_id"] for a in direct_reports] == ["alice-s9"]


def test_create_supervisor_assignment_is_idempotent(client, role_management_headers):
    first = client.post(
        "/supervisor-assignments",
        json={"principal_id": "alice-idem", "supervisor_principal_id": "bob-idem"},
        headers=role_management_headers,
    ).json()
    second = client.post(
        "/supervisor-assignments",
        json={"principal_id": "alice-idem", "supervisor_principal_id": "bob-idem"},
        headers=role_management_headers,
    ).json()
    assert first["id"] == second["id"]


def test_self_supervision_returns_422(client, role_management_headers):
    response = client.post(
        "/supervisor-assignments",
        json={"principal_id": "carol-self", "supervisor_principal_id": "carol-self"},
        headers=role_management_headers,
    )
    assert response.status_code == 422


def test_supervisor_cycle_returns_409(client, role_management_headers):
    """dave-cy -> erin-cy (erin ist Vorgesetzte von dave); der Versuch, den
    umgekehrten Bogen erin-cy -> dave-cy anzulegen, würde einen Zyklus
    erzeugen und muss abgelehnt werden."""
    client.post(
        "/supervisor-assignments",
        json={"principal_id": "dave-cy", "supervisor_principal_id": "erin-cy"},
        headers=role_management_headers,
    )
    response = client.post(
        "/supervisor-assignments",
        json={"principal_id": "erin-cy", "supervisor_principal_id": "dave-cy"},
        headers=role_management_headers,
    )
    assert response.status_code == 409


def test_delete_unknown_supervisor_assignment_returns_404(client, role_management_headers):
    response = client.delete("/supervisor-assignments/999999", headers=role_management_headers)
    assert response.status_code == 404


def test_delete_supervisor_assignment(client, role_management_headers):
    created = client.post(
        "/supervisor-assignments",
        json={"principal_id": "frank-del", "supervisor_principal_id": "grace-del"},
        headers=role_management_headers,
    ).json()

    response = client.delete(
        f"/supervisor-assignments/{created['id']}", headers=role_management_headers
    )
    assert response.status_code == 204

    remaining = client.get("/supervisor-assignments", params={"principal_id": "frank-del"}).json()
    assert remaining == []


def test_supervisor_chain_unions_multiple_upward_paths(client, role_management_headers):
    """Rautenform (matrix/DAG statt einfacher Baum, siehe ADR 0119-Nachfolge-
    ADR fuer P31-S9): heidi berichtet sowohl an ivan als auch an judy, beide
    wiederum an karl - die transitive Kette von heidi muss die Vereinigung
    aller Pfade sein, karl nur einmal enthalten."""
    for principal, supervisor in [
        ("heidi-dag", "ivan-dag"),
        ("heidi-dag", "judy-dag"),
        ("ivan-dag", "karl-dag"),
        ("judy-dag", "karl-dag"),
    ]:
        client.post(
            "/supervisor-assignments",
            json={"principal_id": principal, "supervisor_principal_id": supervisor},
            headers=role_management_headers,
        )

    chain = client.get("/supervisor-chain/heidi-dag").json()
    assert set(chain["supervisor_ids"]) == {"ivan-dag", "judy-dag", "karl-dag"}

    # Ein Principal ohne Vorgesetzte hat eine leere Kette.
    empty_chain = client.get("/supervisor-chain/karl-dag").json()
    assert empty_chain["supervisor_ids"] == []


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


def test_assignment_with_unknown_resource_returns_404(client, role_management_headers):
    role = client.post(
        "/roles", json={"name": "Viewer2", "permissions": ["read"]}, headers=role_management_headers
    ).json()["role"]
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


def test_full_flow_via_api(client, role_management_headers):
    role = client.post(
        "/roles",
        json={"name": "Editor", "permissions": ["read", "write"]},
        headers=role_management_headers,
    ).json()["role"]
    # Seit P17-S3 (4.3/14.2) liefert `POST /role-assignments` das gegatete
    # `RoleAssignmentActionResult` - ohne aktivierte Genehmigungspflicht
    # (Default) bleibt `role_assignment` sofort gesetzt, siehe schemas.py.
    assignment = client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "carol",
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    ).json()["role_assignment"]

    effective = client.get(f"/effective-permissions/carol/{ROOT_RESOURCE_ID}").json()
    # Seit Phase 19 Session 2 ("everyone"-Gruppe, ADR 0067) hat JEDER Principal
    # zusätzlich die an der Wurzelressource geseedete "everyone"-Basisrolle.
    assert set(effective["permissions"]) == {"read", "write", *repository.EVERYONE_ROLE_PERMISSIONS}

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


def test_check_batch_returns_per_resource_result(client, role_management_headers):
    """Batch-Form von /check (P5-S4, Search Service) - eine Resource mit
    Grant, eine unbekannte."""
    role = client.post(
        "/roles",
        json={"name": "BatchCheckRole", "permissions": ["document.read"]},
        headers=role_management_headers,
    ).json()["role"]
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "grace",
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    )

    response = client.post(
        "/check/batch",
        json={
            "principal_id": "grace",
            "permission": "document.read",
            "access_type": "read",
            "resource_ids": [ROOT_RESOURCE_ID, "unbekannte-ressource"],
        },
    )

    assert response.status_code == 200
    results = response.json()["results"]
    assert results[ROOT_RESOURCE_ID] is True
    assert results["unbekannte-ressource"] is False


def test_check_batch_denies_principal_without_assignment(client):
    # Post-Roadmap Phase 38 Session 4 (ADR 0149): `document.read` is now in
    # `EVERYONE_ROLE_PERMISSIONS`, so it no longer distinguishes "no
    # assignment" from "granted via everyone" - a permission deliberately
    # NOT in that list keeps this test's actual intent (a principal with
    # truly no grant is denied) instead of asserting behavior this same
    # session intentionally changed.
    response = client.post(
        "/check/batch",
        json={
            "principal_id": "no-such-principal",
            "permission": "admin.user_management",
            "access_type": "read",
            "resource_ids": [ROOT_RESOURCE_ID],
        },
    )

    assert response.status_code == 200
    assert response.json()["results"][ROOT_RESOURCE_ID] is False


def test_check_batch_respects_blocking_scope_lock(client, role_management_headers):
    role = client.post(
        "/roles",
        json={"name": "BatchLockRole", "permissions": ["document.read"]},
        headers=role_management_headers,
    ).json()["role"]
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "heidi",
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    )
    client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin", "blocks_read": True},
        headers={"X-DMS-Principal": "admin"},
    )

    response = client.post(
        "/check/batch",
        json={
            "principal_id": "heidi",
            "permission": "document.read",
            "access_type": "read",
            "resource_ids": [ROOT_RESOURCE_ID],
        },
    )

    assert response.json()["results"][ROOT_RESOURCE_ID] is False


def test_check_batch_with_empty_resource_ids_returns_empty_results(client):
    response = client.post(
        "/check/batch",
        json={
            "principal_id": "ivan",
            "permission": "document.read",
            "access_type": "read",
            "resource_ids": [],
        },
    )

    assert response.status_code == 200
    assert response.json()["results"] == {}


def test_check_rejects_access_type_mismatch(client):
    """P39-S2 (ADR 0151) - `access_type` (only used for scope-lock blocking
    severity) previously wasn't cross-checked against `permission` at all,
    letting a caller pass `document.write` with `access_type=read`. Only
    permissions following the `.read`/`.write` naming convention are
    validated - see `_validate_access_type`'s own docstring."""
    response = client.get(
        "/check",
        params={
            "principal_id": "ivan",
            "resource_id": ROOT_RESOURCE_ID,
            "permission": "document.write",
            "access_type": "read",
        },
    )
    assert response.status_code == 422


def test_check_allows_permission_without_read_write_suffix(client):
    """Capability-style permissions (e.g. `admin.user_management`) have no
    textual `.read`/`.write` convention to validate against - must stay
    uncheckable, not rejected."""
    response = client.get(
        "/check",
        params={
            "principal_id": "ivan",
            "resource_id": ROOT_RESOURCE_ID,
            "permission": "admin.user_management",
            "access_type": "read",
        },
    )
    assert response.status_code == 200


def test_check_batch_rejects_access_type_mismatch(client):
    response = client.post(
        "/check/batch",
        json={
            "principal_id": "ivan",
            "permission": "document.write",
            "access_type": "read",
            "resource_ids": [ROOT_RESOURCE_ID],
        },
    )
    assert response.status_code == 422


def test_create_role_assignment_publishes_event(client, role_management_headers, monkeypatch):
    """P71-S2: same gap as `create_role` - the direct/default path never
    published anything, only the four-eyes-approved path did."""
    role_id = client.post(
        "/roles",
        json={"name": "Assignable", "permissions": ["read"]},
        headers=role_management_headers,
    ).json()["role"]["id"]

    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.publisher, "publish", fake_publish)

    response = client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "dave",
            "role_id": role_id,
            "resource_id": ROOT_RESOURCE_ID,
        },
    )

    assert response.status_code == 201
    events = [e for e in published if e.event_type == "permission.role_assignment.created"]
    assert len(events) == 1
    assert events[0].payload["principal_id"] == "dave"
    assert events[0].actor == "dave"


def test_delete_role_assignment_publishes_event(client, role_management_headers, monkeypatch):
    """P71-S2: this endpoint never had ANY event at all before this
    (not even via four-eyes - deletion was never a known action type)."""
    role_id = client.post(
        "/roles",
        json={"name": "Revocable", "permissions": ["read"]},
        headers=role_management_headers,
    ).json()["role"]["id"]
    assignment_id = client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "erin",
            "role_id": role_id,
            "resource_id": ROOT_RESOURCE_ID,
        },
    ).json()["role_assignment"]["id"]

    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.publisher, "publish", fake_publish)

    response = client.delete(
        f"/role-assignments/{assignment_id}", headers={"X-DMS-Principal": "admin"}
    )

    assert response.status_code == 204
    events = [e for e in published if e.event_type == "permission.role_assignment.deleted"]
    assert len(events) == 1
    assert events[0].payload["principal_id"] == "erin"
    assert events[0].actor == "admin"


def test_delete_role_assignment_without_principal_header_still_publishes(
    client, role_management_headers, monkeypatch
):
    """The new header is attribution-only, not gating - an existing caller
    that never sends it must keep working identically."""
    role_id = client.post(
        "/roles",
        json={"name": "Revocable2", "permissions": ["read"]},
        headers=role_management_headers,
    ).json()["role"]["id"]
    assignment_id = client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "frank",
            "role_id": role_id,
            "resource_id": ROOT_RESOURCE_ID,
        },
    ).json()["role_assignment"]["id"]

    response = client.delete(f"/role-assignments/{assignment_id}")

    assert response.status_code == 204


def test_list_role_assignments_returns_all(client, role_management_headers):
    role = client.post(
        "/roles",
        json={"name": "Listener", "permissions": ["read"]},
        headers=role_management_headers,
    ).json()["role"]
    created = client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "dave",
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    ).json()["role_assignment"]

    response = client.get("/role-assignments")

    assert response.status_code == 200
    ids = [a["id"] for a in response.json()]
    assert created["id"] in ids


def test_list_role_assignments_filters_by_principal_id(client, role_management_headers):
    role = client.post(
        "/roles",
        json={"name": "Filterable", "permissions": ["read"]},
        headers=role_management_headers,
    ).json()["role"]
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
    # Seit Phase 19 Session 2 ("everyone"-Gruppe, ADR 0067) ist die Liste nicht
    # mehr leer, sondern die an der Wurzelressource geseedete Basisrolle.
    assert after["permissions"] == sorted(repository.EVERYONE_ROLE_PERMISSIONS)


def test_scope_lock_requires_authentication(client):
    """No principal, no write (P85-S2, ADR 0240): 401 before any capability
    check - before P85-S2 the empty body actor reached the check (403)."""
    response = client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": ""},
        headers={"X-DMS-Principal": ""},
    )
    assert response.status_code == 401


def test_scope_lock_with_unknown_resource_returns_404(client, role_management_headers):
    response = client.post(
        "/scope-locks",
        json={"resource_id": "does-not-exist", "locked_by": "admin"},
        headers={"X-DMS-Principal": "admin"},
    )
    assert response.status_code == 404


def test_create_scope_lock_bypassed_by_active_superuser_without_explicit_role(client, monkeypatch):
    """P63-S1: the superuser bypass on `create_scope_lock`. (Its actor source
    was the `locked_by` body field until P85-S2; since then it is the request
    principal, ADR 0240 - the test sends both, as before.)"""

    async def fake_get_active_superuser():
        return True, "root-admin"

    monkeypatch.setattr(app.state.auth_client, "get_active_superuser", fake_get_active_superuser)

    response = client.post(
        "/scope-locks",
        json={"resource_id": "does-not-exist", "locked_by": "root-admin"},
        headers={"X-DMS-Principal": "root-admin"},
    )
    # No explicit `admin.user_management` grant for "root-admin" anywhere in
    # this test - a `404` (not `403`) proves the capability check itself
    # was bypassed and the request proceeded to the resource lookup.
    assert response.status_code == 404


def test_scope_lock_blocks_write_check_with_reason(client, role_management_headers):
    created = client.post(
        "/scope-locks",
        json={
            "resource_id": ROOT_RESOURCE_ID,
            "locked_by": "admin",
            "reason": "Revision läuft",
        },
        headers={"X-DMS-Principal": "admin"},
    ).json()
    assert created["status"] == "created"
    lock = created["scope_lock"]

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

    release = client.request(
        "DELETE",
        f"/scope-locks/{lock['id']}",
        json={"released_by": "admin"},
        headers={"X-DMS-Principal": "admin"},
    )
    assert release.status_code == 200
    assert release.json()["status"] == "released"
    assert release.json()["scope_lock"]["released_by"] == "admin"

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


def test_scope_lock_without_blocks_read_allows_read_access(client, role_management_headers):
    client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"},
        headers={"X-DMS-Principal": "admin"},
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

    assert result["blocked_by_scope_lock"] is False


def test_scope_lock_with_blocks_read_also_blocks_read_access(client, role_management_headers):
    client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin", "blocks_read": True},
        headers={"X-DMS-Principal": "admin"},
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


def test_scope_lock_bypass_capability_overrides_block(client, role_management_headers):
    client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"},
        headers={"X-DMS-Principal": "admin"},
    )
    role = client.post(
        "/roles",
        json={"name": "ScopeLockBypasser", "permissions": ["scope_lock.bypass", "write"]},
        headers=role_management_headers,
    ).json()["role"]
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


def test_list_scope_locks_endpoint(client, role_management_headers):
    client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"},
        headers={"X-DMS-Principal": "admin"},
    )

    response = client.get("/scope-locks", params={"resource_id": ROOT_RESOURCE_ID})

    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_effective_scope_locks_endpoint(client, role_management_headers):
    client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"},
        headers={"X-DMS-Principal": "admin"},
    )

    response = client.get(f"/scope-locks/effective/{ROOT_RESOURCE_ID}")

    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_release_unknown_scope_lock_returns_404(client, role_management_headers):
    response = client.request(
        "DELETE",
        "/scope-locks/999999",
        json={"released_by": "admin"},
        headers={"X-DMS-Principal": "admin"},
    )
    assert response.status_code == 404


def test_approval_config_defaults_to_false_and_is_settable(client, role_management_headers):
    default = client.get("/approval-config/document.force_unlock").json()
    assert default["requires_approval"] is False

    updated = client.put(
        "/approval-config/document.force_unlock",
        json={"requires_approval": True},
        headers=role_management_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["requires_approval"] is True

    listed = client.get("/approval-config").json()
    assert any(
        c["action_type"] == "document.force_unlock" and c["requires_approval"] is True
        for c in listed
    )


def test_put_approval_config_requires_authentication(client):
    """P32-S1 (ADR 0130) - previously ungated entirely (ADR 0089), now
    self-gated like `POST`/`PUT /roles`."""
    response = client.put(
        "/approval-config/document.force_unlock", json={"requires_approval": True}
    )
    assert response.status_code == 401


def test_put_approval_config_returns_403_without_permission(client):
    response = client.put(
        "/approval-config/document.force_unlock",
        json={"requires_approval": True},
        headers={"X-DMS-Principal": "nobody"},
    )
    assert response.status_code == 403


def test_approval_request_lifecycle_publishes_events_with_actor(client, monkeypatch):
    """First-class Actor-Feld (5.4b-Voraussetzung, seit P7-S2) - Antrag und
    Genehmigung tragen die jeweils handelnde Person als `actor`, nicht nur
    ad-hoc unter payload-Schlüsseln wie `initiated_by`/`approved_by`."""
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.publisher, "publish", fake_publish)

    created = client.post(
        "/approval-requests",
        json={"action_type": "document.force_unlock", "initiated_by": "alice", "payload": {"x": 1}},
        headers={"X-DMS-Principal": "alice"},
    )
    assert created.status_code == 201
    request_id = created.json()["id"]

    client.post(
        f"/approval-requests/{request_id}/approve",
        json={"approved_by": "bob"},
        headers={"X-DMS-Principal": "bob"},
    )

    requested = [e for e in published if e.event_type == "permission.approval.requested"]
    approved = [e for e in published if e.event_type == "permission.approval.approved"]
    assert requested[0].actor == "alice"
    assert approved[0].actor == "bob"


def test_approval_request_event_names_its_recipients(client, monkeypatch, role_management_headers):
    """P90-S3 (ADR 0247): `permission.approval.requested` carries who is to be
    told, resolved when the request is made; the preview endpoint shows the
    same list, and the config keeps fields that weren't sent."""
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.publisher, "publish", fake_publish)
    role_name = f"freigabe-{uuid.uuid4().hex[:8]}"
    role = client.post(
        "/roles",
        json={"name": role_name, "description": "", "permissions": []},
        headers=role_management_headers,
    ).json()["role"]
    for user in ("carla", "alice"):
        client.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": user,
                "role_id": role["id"],
                "resource_id": "root",
            },
            headers=role_management_headers,
        )
    action = f"test.action.{uuid.uuid4().hex[:6]}"
    assert (
        client.put(
            f"/approval-config/{action}",
            json={"requires_approval": True, "recipient_role": "gibt-es-nicht"},
            headers=role_management_headers,
        ).status_code
        == 422
    )
    client.put(
        f"/approval-config/{action}",
        json={"requires_approval": True, "recipient_role": role_name},
        headers=role_management_headers,
    )
    # Toggling only `requires_approval` keeps the recipient role.
    toggled = client.put(
        f"/approval-config/{action}",
        json={"requires_approval": True},
        headers=role_management_headers,
    ).json()
    assert toggled["recipient_role"] == role_name

    preview = client.get(
        f"/approval-config/{action}/recipients", headers=role_management_headers
    ).json()
    assert (preview["source"], preview["recipients"]) == ("recipient_role", ["alice", "carla"])
    assert client.get(f"/approval-config/{action}/recipients").status_code in (401, 403)

    client.post(
        "/approval-requests",
        json={"action_type": action, "payload": {}},
        headers={"X-DMS-Principal": "alice"},
    )
    (requested,) = [e for e in published if e.event_type == "permission.approval.requested"]
    # The initiator isn't told about their own request.
    assert requested.payload["recipients"] == ["carla"]
    assert requested.payload["recipient_source"] == "recipient_role"
    assert requested.payload["recipients_truncated"] is False


def test_approval_request_lifecycle_via_api(client):
    created = client.post(
        "/approval-requests",
        json={"action_type": "document.force_unlock", "initiated_by": "alice", "payload": {"x": 1}},
        headers={"X-DMS-Principal": "alice"},
    )
    assert created.status_code == 201
    request = created.json()
    assert request["status"] == "pending"

    fetched = client.get(f"/approval-requests/{request['id']}")
    assert fetched.status_code == 200

    same_person = client.post(
        f"/approval-requests/{request['id']}/approve",
        json={"approved_by": "alice"},
        headers={"X-DMS-Principal": "alice"},
    )
    assert same_person.status_code == 403

    approved = client.post(
        f"/approval-requests/{request['id']}/approve",
        json={"approved_by": "bob"},
        headers={"X-DMS-Principal": "bob"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == "bob"

    double_decision = client.post(
        f"/approval-requests/{request['id']}/approve",
        json={"approved_by": "carol"},
        headers={"X-DMS-Principal": "carol"},
    )
    assert double_decision.status_code == 409


def test_approval_request_reject_allows_initiator(client):
    request = client.post(
        "/approval-requests",
        json={"action_type": "document.force_unlock", "initiated_by": "alice", "payload": {}},
        headers={"X-DMS-Principal": "alice"},
    ).json()

    rejected = client.post(
        f"/approval-requests/{request['id']}/reject",
        json={"rejected_by": "alice", "reason": "Doch nicht nötig"},
        headers={"X-DMS-Principal": "alice"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_get_unknown_approval_request_returns_404(client):
    response = client.get("/approval-requests/does-not-exist")
    assert response.status_code == 404


def test_create_scope_lock_with_approval_required_defers_creation(client, role_management_headers):
    client.put(
        "/approval-config/permission.scope_lock.create",
        json={"requires_approval": True},
        headers=role_management_headers,
    )

    response = client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin", "reason": "Migration"},
        headers={"X-DMS-Principal": "admin"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["approval_request_id"] is not None
    assert body["scope_lock"] is None

    # Die Sperre existiert noch nicht - Genehmigung erfolgt asynchron ueber
    # das Event (siehe test_approval_consumer.py fuer die Konsumentenlogik).
    active = client.get(f"/scope-locks/effective/{ROOT_RESOURCE_ID}").json()
    assert active == []


def test_release_scope_lock_with_approval_required_defers_release(client, role_management_headers):
    created = client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"},
        headers={"X-DMS-Principal": "admin"},
    ).json()
    lock_id = created["scope_lock"]["id"]

    client.put(
        "/approval-config/permission.scope_lock.release",
        json={"requires_approval": True},
        headers=role_management_headers,
    )

    response = client.request(
        "DELETE",
        f"/scope-locks/{lock_id}",
        json={"released_by": "admin"},
        headers={"X-DMS-Principal": "admin"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["approval_request_id"] is not None

    still_active = client.get(f"/scope-locks/effective/{ROOT_RESOURCE_ID}").json()
    assert len(still_active) == 1


def test_create_role_assignment_with_approval_required_defers_creation(
    client, role_management_headers
):
    """Vier-Augen-Retrofit für Rollenzuweisung (4.3/14.2, seit P17-S3) -
    identisches Muster wie die Bereichssperren oben, nur mit
    `permission.role_assignment.create`."""
    role = client.post(
        "/roles",
        json={"name": "GatedAssigneeRole", "permissions": ["read"]},
        headers=role_management_headers,
    ).json()["role"]
    client.put(
        "/approval-config/permission.role_assignment.create",
        json={"requires_approval": True},
        headers=role_management_headers,
    )

    response = client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "gina",
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["approval_request_id"] is not None
    assert body["role_assignment"] is None

    # Die Zuweisung existiert noch nicht - Genehmigung erfolgt asynchron ueber
    # das Event (siehe test_approval_consumer.py fuer die Konsumentenlogik).
    assignments = client.get("/role-assignments", params={"principal_id": "gina"}).json()
    assert assignments == []


def test_superuser_activate_is_preseeded_with_required_permission(client):
    """4.6: Break-Glass-Aktivierung ist - anders als Force-Unlock/Scope-Lock
    (P6-S4) - schon beim ersten Start gegated, nicht erst per Admin-Opt-in."""
    config = client.get("/approval-config/auth.superuser.activate").json()

    assert config["requires_approval"] is True
    assert config["required_permission"] == "breakglass.approve"


def test_domain_admin_roles_are_preseeded(client):
    roles = {role["name"]: role for role in client.get("/roles").json()}

    assert roles["domain-admin-users"]["permissions"] == ["admin.user_management"]
    assert roles["breakglass-approver"]["permissions"] == ["breakglass.approve"]


def _grant_permission_via_api(client, headers, *, principal_id, permissions):
    role = client.post(
        "/roles",
        json={"name": f"role-for-{principal_id}", "permissions": permissions},
        headers=headers,
    ).json()["role"]
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": principal_id,
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    )


def test_create_approval_request_without_required_permission_is_forbidden(client):
    response = client.post(
        "/approval-requests",
        json={"action_type": "auth.superuser.activate", "initiated_by": "alice", "payload": {}},
        headers={"X-DMS-Principal": "alice"},
    )

    assert response.status_code == 403


def test_approve_approval_request_without_required_permission_is_forbidden(
    client, role_management_headers
):
    _grant_permission_via_api(
        client, role_management_headers, principal_id="alice", permissions=["breakglass.approve"]
    )
    request = client.post(
        "/approval-requests",
        json={"action_type": "auth.superuser.activate", "initiated_by": "alice", "payload": {}},
        headers={"X-DMS-Principal": "alice"},
    ).json()

    response = client.post(
        f"/approval-requests/{request['id']}/approve",
        json={"approved_by": "bob"},
        headers={"X-DMS-Principal": "bob"},
    )

    assert response.status_code == 403


def test_break_glass_activation_via_api_with_two_group_members(client, role_management_headers):
    _grant_permission_via_api(
        client, role_management_headers, principal_id="alice", permissions=["breakglass.approve"]
    )
    _grant_permission_via_api(
        client, role_management_headers, principal_id="bob", permissions=["breakglass.approve"]
    )
    request = client.post(
        "/approval-requests",
        json={"action_type": "auth.superuser.activate", "initiated_by": "alice", "payload": {}},
        headers={"X-DMS-Principal": "alice"},
    ).json()

    response = client.post(
        f"/approval-requests/{request['id']}/approve",
        json={"approved_by": "bob"},
        headers={"X-DMS-Principal": "bob"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_get_maintenance_mode_defaults_to_inactive(client):
    response = client.get("/maintenance-mode")

    assert response.status_code == 200
    assert response.json()["active"] is False


def test_trigger_maintenance_mode_without_permission_is_forbidden(client):
    response = client.post(
        "/maintenance-mode/trigger",
        json={"triggered_by": "alice", "reason": "Verdacht"},
        headers={"X-DMS-Principal": "alice"},
    )

    assert response.status_code == 403


def test_trigger_maintenance_mode_activates_directly_with_permission(
    client, role_management_headers
):
    _grant_permission_via_api(
        client,
        role_management_headers,
        principal_id="alice",
        permissions=["system.not_shutdown.trigger"],
    )

    response = client.post(
        "/maintenance-mode/trigger",
        json={"triggered_by": "alice", "reason": "Verdacht"},
        headers={"X-DMS-Principal": "alice"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "activated"
    assert body["maintenance_mode"]["active"] is True
    assert body["maintenance_mode"]["triggered_by"] == "alice"

    status = client.get("/maintenance-mode").json()
    assert status["active"] is True


def test_trigger_maintenance_mode_defers_when_approval_required(client, role_management_headers):
    _grant_permission_via_api(
        client,
        role_management_headers,
        principal_id="alice",
        permissions=["system.not_shutdown.trigger"],
    )
    client.put(
        "/approval-config/system.not_shutdown.trigger",
        json={"requires_approval": True, "required_permission": "system.not_shutdown.trigger"},
        headers=role_management_headers,
    )

    response = client.post(
        "/maintenance-mode/trigger",
        json={"triggered_by": "alice", "reason": "Verdacht"},
        headers={"X-DMS-Principal": "alice"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "pending_approval"
    assert body["approval_request_id"] is not None

    status = client.get("/maintenance-mode").json()
    assert status["active"] is False


def test_trigger_maintenance_mode_via_approval_flow_activates_after_second_approver(
    client, role_management_headers
):
    _grant_permission_via_api(
        client,
        role_management_headers,
        principal_id="alice",
        permissions=["system.not_shutdown.trigger"],
    )
    _grant_permission_via_api(
        client,
        role_management_headers,
        principal_id="bob",
        permissions=["system.not_shutdown.trigger"],
    )
    client.put(
        "/approval-config/system.not_shutdown.trigger",
        json={"requires_approval": True, "required_permission": "system.not_shutdown.trigger"},
        headers=role_management_headers,
    )
    request = client.post(
        "/maintenance-mode/trigger",
        json={"triggered_by": "alice", "reason": "Verdacht"},
        headers={"X-DMS-Principal": "alice"},
    ).json()

    approval = client.post(
        f"/approval-requests/{request['approval_request_id']}/approve",
        json={"approved_by": "bob"},
        headers={"X-DMS-Principal": "bob"},
    )

    assert approval.status_code == 200
    status = client.get("/maintenance-mode").json()
    assert status["active"] is True
    assert status["triggered_by"] == "alice"


def test_lift_maintenance_mode_without_active_superuser_is_forbidden(
    client, role_management_headers
):
    """Echter Aufruf gegen den laufenden `auth-service` (kein Mocking,
    gleiches Prinzip wie in P6-S4/S5) - ohne aktivierten Superuser muss das
    Aufheben in jedem Fall abgelehnt werden."""
    _grant_permission_via_api(
        client,
        role_management_headers,
        principal_id="alice",
        permissions=["system.not_shutdown.trigger"],
    )
    client.post(
        "/maintenance-mode/trigger",
        json={"triggered_by": "alice", "reason": None},
        headers={"X-DMS-Principal": "alice"},
    )

    response = client.post(
        "/maintenance-mode/lift", json={"lifted_by": "alice"}, headers={"X-DMS-Principal": "alice"}
    )

    assert response.status_code == 403
    status = client.get("/maintenance-mode").json()
    assert status["active"] is True


# --- Stellvertretung bei Abwesenheit (4.4a, P14-S11) -------------------------


def _delegation_window():
    now = datetime.now(UTC)
    return {
        "starts_at": (now - timedelta(hours=1)).isoformat(),
        "ends_at": (now + timedelta(days=1)).isoformat(),
    }


def test_create_delegation_requires_principal_header(client):
    response = client.post(
        "/delegations", json={"deputy_principal_id": "bob", **_delegation_window()}
    )
    assert response.status_code == 401


def test_create_delegation_rejects_ends_at_before_starts_at(client):
    now = datetime.now(UTC)
    response = client.post(
        "/delegations",
        json={
            "deputy_principal_id": "bob",
            "starts_at": now.isoformat(),
            "ends_at": (now - timedelta(hours=1)).isoformat(),
        },
        headers={"X-DMS-Principal": "alice"},
    )
    assert response.status_code == 400


def test_create_delegation_succeeds_and_publishes_event(client, monkeypatch):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.publisher, "publish", fake_publish)

    response = client.post(
        "/delegations",
        json={"deputy_principal_id": "bob", **_delegation_window()},
        headers={"X-DMS-Principal": "alice"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["delegator_principal_id"] == "alice"
    assert body["deputy_principal_id"] == "bob"
    assert body["revoked_at"] is None

    created_events = [e for e in published if e.event_type == "permission.delegation.created"]
    assert len(created_events) == 1
    assert created_events[0].actor == "alice"


def test_list_delegations_filters_by_query_params(client):
    client.post(
        "/delegations",
        json={"deputy_principal_id": "bob", **_delegation_window()},
        headers={"X-DMS-Principal": "alice"},
    )
    client.post(
        "/delegations",
        json={"deputy_principal_id": "carol", **_delegation_window()},
        headers={"X-DMS-Principal": "alice"},
    )

    response = client.get(
        "/delegations",
        params={"deputy_principal_id": "bob"},
        headers={"X-DMS-Principal": "bob"},
    )

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["deputy_principal_id"] == "bob"


def test_list_delegations_without_principal_header_is_401(client):
    response = client.get("/delegations")
    assert response.status_code == 401


def test_list_delegations_with_no_filter_requires_admin_permission(client):
    """Post-Roadmap Phase 38 Session 3: querying with NEITHER filter set
    (the installation-wide overview, `admin-ui`'s `DelegationsAdmin.tsx`)
    now requires `admin.user_management` - previously fully ungated."""
    response = client.get("/delegations", headers={"X-DMS-Principal": "alice"})
    assert response.status_code == 403


def test_list_delegations_with_no_filter_succeeds_for_admin(client, role_management_headers):
    response = client.get("/delegations", headers=role_management_headers)
    assert response.status_code == 200


def test_list_delegations_for_someone_elses_principal_is_forbidden(client):
    """Closes a snooping vector: passing another principal's ID as the
    filter (instead of one's own) is not self-service either."""
    response = client.get(
        "/delegations",
        params={"delegator_principal_id": "alice"},
        headers={"X-DMS-Principal": "eve"},
    )
    assert response.status_code == 403


def test_list_active_delegations_for_deputy(client):
    client.post(
        "/delegations",
        json={"deputy_principal_id": "bob", **_delegation_window()},
        headers={"X-DMS-Principal": "alice"},
    )

    response = client.get("/delegations/active-for-deputy/bob")

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["delegator_principal_id"] == "alice"


def test_check_delegation_allowed_and_denied(client):
    client.post(
        "/delegations",
        json={"deputy_principal_id": "bob", **_delegation_window()},
        headers={"X-DMS-Principal": "alice"},
    )

    allowed = client.get(
        "/delegations/check",
        params={"deputy_principal_id": "bob", "delegator_principal_id": "alice"},
    )
    assert allowed.json() == {"allowed": True}

    denied = client.get(
        "/delegations/check",
        params={"deputy_principal_id": "bob", "delegator_principal_id": "someone-else"},
    )
    assert denied.json() == {"allowed": False}


def test_revoke_delegation_requires_creator_or_admin_role(client, role_management_headers):
    created = client.post(
        "/delegations",
        json={"deputy_principal_id": "bob", **_delegation_window()},
        headers={"X-DMS-Principal": "alice"},
    ).json()

    forbidden = client.delete(
        f"/delegations/{created['id']}", headers={"X-DMS-Principal": "someone-else"}
    )
    assert forbidden.status_code == 403

    # `role_management_headers` grants "admin.user_management" to the
    # principal "admin" (Post-Roadmap Phase 38 Session 3: this endpoint's
    # admin branch now checks the real capability instead of the legacy
    # `X-DMS-Roles`/`delegation_revoke_admin_role` string comparison).
    admin = client.delete(f"/delegations/{created['id']}", headers=role_management_headers)
    assert admin.status_code == 204


def test_revoke_delegation_by_delegator_succeeds_and_publishes_event(client, monkeypatch):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.publisher, "publish", fake_publish)

    created = client.post(
        "/delegations",
        json={"deputy_principal_id": "bob", **_delegation_window()},
        headers={"X-DMS-Principal": "alice"},
    ).json()

    response = client.delete(f"/delegations/{created['id']}", headers={"X-DMS-Principal": "alice"})
    assert response.status_code == 204

    revoked_events = [e for e in published if e.event_type == "permission.delegation.revoked"]
    assert len(revoked_events) == 1

    check = client.get(
        "/delegations/check",
        params={"deputy_principal_id": "bob", "delegator_principal_id": "alice"},
    )
    assert check.json() == {"allowed": False}


def test_revoke_delegation_unknown_id_returns_404(client):
    response = client.delete("/delegations/does-not-exist", headers={"X-DMS-Principal": "alice"})
    assert response.status_code == 404


# --- Org-hierarchy-based dynamic access grants (14.2, Post-Roadmap Phase 31
# Session 10) --------------------------------------------------------------


def test_org_hierarchy_grant_supervisor_creates_one_delegation_per_direct_supervisor(
    client, role_management_headers
):
    """oskar-p10 hat zwei direkte Vorgesetzte (Matrix-Organisation, wie in
    P31-S9 vom Nutzer gewählt) - "supervisor" muss beiden eine Delegation
    einräumen, nicht nur einem."""
    client.post(
        "/supervisor-assignments",
        json={"principal_id": "oskar-p10", "supervisor_principal_id": "petra-p10"},
        headers=role_management_headers,
    )
    client.post(
        "/supervisor-assignments",
        json={"principal_id": "oskar-p10", "supervisor_principal_id": "quirin-p10"},
        headers=role_management_headers,
    )

    response = client.post(
        "/org-hierarchy-grants",
        json={
            "principal_id": "oskar-p10",
            "grant_kind": "supervisor",
            "process_definition_id": 1,
            "ends_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body["deputy_principal_ids"]) == {"petra-p10", "quirin-p10"}
    assert len(body["delegation_ids"]) == 2

    delegations = client.get(
        "/delegations",
        params={"delegator_principal_id": "oskar-p10"},
        headers={"X-DMS-Principal": "oskar-p10"},
    ).json()
    assert {d["deputy_principal_id"] for d in delegations} == {"petra-p10", "quirin-p10"}
    assert all(d["scope_process_definition_ids"] == [1] for d in delegations)


def test_org_hierarchy_grant_with_case_resource_id_narrows_the_delegation_scope(
    client, role_management_headers
):
    """Post-Roadmap Phase 39 Session 4 (ADR 0154) - when the caller
    (workflow-service) has already resolved the triggering instance's
    `business_key` to a real case, the resulting delegation is scoped to
    BOTH the process-definition family AND that specific case."""
    client.post(
        "/supervisor-assignments",
        json={"principal_id": "ulla-p39s4", "supervisor_principal_id": "viktor-p39s4"},
        headers=role_management_headers,
    )

    response = client.post(
        "/org-hierarchy-grants",
        json={
            "principal_id": "ulla-p39s4",
            "grant_kind": "supervisor",
            "process_definition_id": 1,
            "ends_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
            "case_resource_id": "case-p39s4-1",
        },
    )

    assert response.status_code == 201
    delegations = client.get(
        "/delegations",
        params={"delegator_principal_id": "ulla-p39s4"},
        headers={"X-DMS-Principal": "ulla-p39s4"},
    ).json()
    assert len(delegations) == 1
    assert delegations[0]["scope_process_definition_ids"] == [1]
    assert delegations[0]["scope_case_resource_ids"] == ["case-p39s4-1"]

    assert (
        client.get(
            "/delegations/check",
            params={
                "deputy_principal_id": "viktor-p39s4",
                "delegator_principal_id": "ulla-p39s4",
                "process_definition_id": 1,
                "case_resource_id": "case-p39s4-1",
            },
        ).json()["allowed"]
        is True
    )
    # A different case in the same process-definition family - the extra
    # dimension makes the grant strictly narrower, not just process-wide.
    assert (
        client.get(
            "/delegations/check",
            params={
                "deputy_principal_id": "viktor-p39s4",
                "delegator_principal_id": "ulla-p39s4",
                "process_definition_id": 1,
                "case_resource_id": "case-p39s4-other",
            },
        ).json()["allowed"]
        is False
    )


def test_org_hierarchy_grant_supervisor_chain_unions_diamond_dag(client, role_management_headers):
    for principal, supervisor in [
        ("rosa-p10", "sven-p10"),
        ("rosa-p10", "tanja-p10"),
        ("sven-p10", "ulf-p10"),
        ("tanja-p10", "ulf-p10"),
    ]:
        client.post(
            "/supervisor-assignments",
            json={"principal_id": principal, "supervisor_principal_id": supervisor},
            headers=role_management_headers,
        )

    response = client.post(
        "/org-hierarchy-grants",
        json={
            "principal_id": "rosa-p10",
            "grant_kind": "supervisor_chain",
            "process_definition_id": 2,
            "ends_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert set(body["deputy_principal_ids"]) == {"sven-p10", "tanja-p10", "ulf-p10"}
    assert len(body["delegation_ids"]) == 3


def test_org_hierarchy_grant_org_unit_grants_every_group_member_except_self(
    client, role_management_headers
):
    """Seit Post-Roadmap Phase 35 Session 1 (ADR 0143) zählt nur eine
    `is_org_unit=True`-geflaggte Gruppe - vorher (ADR 0120/0121) jede
    Gruppe, unioned, ungeachtet eines Flags, das es noch nicht gab."""
    group = client.post(
        "/groups",
        json={"name": "OrgUnitP10", "is_org_unit": True},
        headers=role_management_headers,
    ).json()
    for principal_id in ("viktor-p10", "wanda-p10", "xaver-p10"):
        client.post(
            f"/groups/{group['id']}/members",
            json={"principal_id": principal_id},
            headers=role_management_headers,
        )

    response = client.post(
        "/org-hierarchy-grants",
        json={
            "principal_id": "viktor-p10",
            "grant_kind": "org_unit",
            "process_definition_id": 3,
            "ends_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        },
    )

    assert response.status_code == 201
    body = response.json()
    # viktor-p10 selbst ist ausgeschlossen - eine Delegation an sich selbst wäre sinnlos.
    assert set(body["deputy_principal_ids"]) == {"wanda-p10", "xaver-p10"}

    delegations = client.get(
        "/delegations",
        params={"delegator_principal_id": "viktor-p10"},
        headers={"X-DMS-Principal": "viktor-p10"},
    ).json()
    assert all(d["grant_kind"] == "org_unit" for d in delegations)


def test_org_hierarchy_grant_org_unit_ignores_membership_in_an_unflagged_group(
    client, role_management_headers
):
    """Die Vorher-Semantik ("jede Gruppe zählt") darf nicht mehr gelten -
    eine Gruppe ohne `is_org_unit=True` liefert keine Deputies, auch wenn
    das Zielprinzipal echte Mitglieder in ihr hat."""
    group = client.post(
        "/groups", json={"name": "NotAnOrgUnitP35"}, headers=role_management_headers
    ).json()
    assert group["is_org_unit"] is False
    for principal_id in ("noa-p35", "otto-p35"):
        client.post(
            f"/groups/{group['id']}/members",
            json={"principal_id": principal_id},
            headers=role_management_headers,
        )

    response = client.post(
        "/org-hierarchy-grants",
        json={
            "principal_id": "noa-p35",
            "grant_kind": "org_unit",
            "process_definition_id": 30,
            "ends_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        },
    )

    assert response.status_code == 201
    assert response.json() == {"delegation_ids": [], "deputy_principal_ids": []}


def test_group_patch_toggles_is_org_unit(client, role_management_headers):
    group = client.post(
        "/groups", json={"name": "TogglableP35"}, headers=role_management_headers
    ).json()
    assert group["is_org_unit"] is False

    response = client.patch(
        f"/groups/{group['id']}",
        json={"is_org_unit": True},
        headers=role_management_headers,
    )
    assert response.status_code == 200
    assert response.json()["is_org_unit"] is True

    listed = next(g for g in client.get("/groups").json() if g["id"] == group["id"])
    assert listed["is_org_unit"] is True


def test_group_patch_requires_authentication(client):
    response = client.patch("/groups/does-not-exist", json={"is_org_unit": True}, headers={})
    assert response.status_code == 401


def test_group_patch_returns_403_without_permission(client):
    response = client.patch(
        "/groups/does-not-exist",
        json={"is_org_unit": True},
        headers={"X-DMS-Principal": "someone-without-role-admin"},
    )
    assert response.status_code == 403


def test_group_patch_unknown_id_returns_404(client, role_management_headers):
    response = client.patch(
        "/groups/does-not-exist",
        json={"is_org_unit": True},
        headers=role_management_headers,
    )
    assert response.status_code == 404


def test_self_service_delegation_has_no_grant_kind(client):
    """Reine Abgrenzung: eine über `POST /delegations` selbst angelegte
    Delegation trägt `grant_kind: null`, nur eine über
    `POST /org-hierarchy-grants` automatisch erzeugte trägt einen Wert."""
    response = client.post(
        "/delegations",
        json={"deputy_principal_id": "self-service-deputy-p35", **_delegation_window()},
        headers={"X-DMS-Principal": "self-service-delegator-p35"},
    )
    assert response.status_code == 201
    assert response.json()["grant_kind"] is None


def test_org_hierarchy_grant_returns_empty_result_without_error_for_unconfigured_principal(client):
    """Eine Person ohne konfigurierte Vorgesetzte/Gruppenmitgliedschaft
    erhält ein leeres, aber erfolgreiches Ergebnis - kein Fehler."""
    response = client.post(
        "/org-hierarchy-grants",
        json={
            "principal_id": "yvonne-lonely-p10",
            "grant_kind": "supervisor",
            "process_definition_id": 4,
            "ends_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        },
    )

    assert response.status_code == 201
    assert response.json() == {"delegation_ids": [], "deputy_principal_ids": []}


def test_revoke_org_hierarchy_grant_revokes_the_underlying_delegation(
    client, role_management_headers
):
    client.post(
        "/supervisor-assignments",
        json={"principal_id": "zora-p10", "supervisor_principal_id": "adam-p10"},
        headers=role_management_headers,
    )
    created = client.post(
        "/org-hierarchy-grants",
        json={
            "principal_id": "zora-p10",
            "grant_kind": "supervisor",
            "process_definition_id": 5,
            "ends_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        },
    ).json()
    delegation_id = created["delegation_ids"][0]

    response = client.delete(f"/org-hierarchy-grants/{delegation_id}")
    assert response.status_code == 204

    still_active = client.get(
        "/delegations/check",
        params={"deputy_principal_id": "adam-p10", "delegator_principal_id": "zora-p10"},
    ).json()
    assert still_active["allowed"] is False


def test_revoke_org_hierarchy_grant_unknown_id_returns_404(client):
    response = client.delete("/org-hierarchy-grants/does-not-exist")
    assert response.status_code == 404


# --- Acting identity from the principal (P85-S2, ADR 0240) -------------------


def test_approver_cannot_spoof_another_name_to_approve_own_request(client):
    request = client.post(
        "/approval-requests",
        json={"action_type": "spoof.test", "initiated_by": "alice", "payload": {}},
        headers={"X-DMS-Principal": "alice"},
    ).json()
    # Alice claims to be bob in the body - the principal decides.
    response = client.post(
        f"/approval-requests/{request['id']}/approve",
        json={"approved_by": "bob"},
        headers={"X-DMS-Principal": "alice"},
    )
    assert response.status_code == 403


def test_initiator_is_the_principal_not_the_body_field(client):
    request = client.post(
        "/approval-requests",
        json={"action_type": "spoof.test", "initiated_by": "someone-else", "payload": {}},
        headers={"X-DMS-Principal": "alice"},
    ).json()
    assert request["initiated_by"] == "alice"


def test_reject_requires_the_approval_entitlement(client, role_management_headers):
    """Before P85-S2 anyone could reject any pending request."""
    _grant_permission_via_api(
        client, role_management_headers, principal_id="alice", permissions=["breakglass.approve"]
    )
    request = client.post(
        "/approval-requests",
        json={"action_type": "auth.superuser.activate", "payload": {}},
        headers={"X-DMS-Principal": "alice"},
    ).json()

    outsider = client.post(
        f"/approval-requests/{request['id']}/reject",
        json={"rejected_by": "alice"},
        headers={"X-DMS-Principal": "mallory"},
    )
    assert outsider.status_code == 403

    withdrawn = client.post(
        f"/approval-requests/{request['id']}/reject",
        json={},
        headers={"X-DMS-Principal": "alice"},
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["rejected_by"] == "alice"


def test_service_may_create_a_request_on_behalf_of_a_user(client):
    request = client.post(
        "/approval-requests",
        json={"action_type": "spoof.test", "payload": {}},
        headers={"X-DMS-Principal": "document-service", "X-DMS-On-Behalf-Of": "alice"},
    ).json()
    assert request["initiated_by"] == "alice"


def test_a_user_cannot_act_on_behalf_of_another_user(client):
    request = client.post(
        "/approval-requests",
        json={"action_type": "spoof.test", "payload": {}},
        headers={"X-DMS-Principal": "mallory", "X-DMS-On-Behalf-Of": "alice"},
    ).json()
    assert request["initiated_by"] == "mallory"


def test_writes_without_a_principal_return_401(client):
    for method, path, body in [
        ("post", "/approval-requests", {"action_type": "spoof.test", "payload": {}}),
        ("post", "/approval-requests/x/approve", {}),
        ("post", "/approval-requests/x/reject", {}),
        ("delete", "/scope-locks/1", None),
        ("post", "/maintenance-mode/trigger", {"reason": "x"}),
        ("post", "/maintenance-mode/lift", {}),
    ]:
        kwargs = {"json": body} if body is not None else {}
        response = client.request(method.upper(), path, **kwargs)
        assert response.status_code == 401, (method, path, response.text)


def test_on_behalf_request_event_names_the_service_as_actor(client, monkeypatch):
    """P85-S2 (ADR 0240): a service creating a request for a user - the event
    says who acted (the service) and for whom (the user), the same meaning
    `on_behalf_of` already has for delegations."""
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.publisher, "publish", fake_publish)

    created = client.post(
        "/approval-requests",
        json={"action_type": "document.delete", "payload": {}},
        headers={"X-DMS-Principal": "document-service", "X-DMS-On-Behalf-Of": "alice"},
    )
    assert created.json()["initiated_by"] == "alice"
    requested = [e for e in published if e.event_type == "permission.approval.requested"]
    assert requested[0].actor == "document-service"
    assert requested[0].on_behalf_of == "alice"


def test_metrics_count_cache_hits_and_misses(client):
    """P88-S2b: the first lookup for a principal misses (and is timed), the
    same lookup again hits (the sensor config is unreachable in tests:
    fail-open, all on)."""

    def value(text: str, needle: str) -> float:
        line = next((line for line in text.splitlines() if line.startswith(needle)), None)
        return float(line.split()[-1]) if line else 0.0

    principal = f"metrics-probe-{uuid.uuid4().hex[:8]}"
    params = {"principal_id": principal, "resource_id": ROOT_RESOURCE_ID, "permission": "x"}
    before = client.get("/metrics").text
    client.get("/check", params=params)
    client.get("/check", params=params)
    after = client.get("/metrics").text

    miss = 'permission_cache_lookups_total{result="miss"}'
    hit = 'permission_cache_lookups_total{result="hit"}'
    assert value(after, miss) >= value(before, miss) + 1
    assert value(after, hit) >= value(before, hit) + 1
    assert value(after, "permission_cache_rebuild_duration_seconds_count") >= 1


def test_check_batch_matches_single_checks_across_cache_states(client, role_management_headers):
    """P91-S4 (ADR 0259): the batch reads cached entries in bulk and computes
    only misses. Its answers must equal `/check`'s for warm and cold entries,
    and a permission change (new cache generation) must not be answered from
    stale rows."""
    for resource_id, parent in (
        ("batch-a", ROOT_RESOURCE_ID),
        ("batch-b", ROOT_RESOURCE_ID),
        ("batch-c", "batch-b"),
    ):
        client.post(
            "/resources",
            json={"resource_id": resource_id, "parent_id": parent, "resource_type": "folder"},
        )
    client.patch("/resources/batch-b", json={"inherit": False})
    role = client.post(
        "/roles",
        json={"name": "BatchCacheRole", "permissions": ["folder.read"]},
        headers=role_management_headers,
    ).json()["role"]
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "heidi",
            "role_id": role["id"],
            "resource_id": ROOT_RESOURCE_ID,
        },
    )

    def single(resource_id: str) -> bool:
        return client.get(
            "/check",
            params={
                "principal_id": "heidi",
                "resource_id": resource_id,
                "permission": "folder.read",
                "access_type": "read",
            },
        ).json()["allowed"]

    def batch() -> dict[str, bool]:
        return client.post(
            "/check/batch",
            json={
                "principal_id": "heidi",
                "permission": "folder.read",
                "access_type": "read",
                "resource_ids": ["batch-a", "batch-b", "batch-c", "batch-a"],
            },
        ).json()["results"]

    single("batch-a")  # warm one entry, leave the others cold
    expected = {r: single(r) for r in ("batch-a", "batch-b", "batch-c")}
    assert expected == {"batch-a": True, "batch-b": False, "batch-c": False}
    assert batch() == expected

    # A grant on the isolated branch bumps the generation; the batch must
    # not answer from the now stale rows.
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "heidi",
            "role_id": role["id"],
            "resource_id": "batch-b",
        },
    )
    assert batch() == {"batch-a": True, "batch-b": True, "batch-c": True}


def test_principal_memberships_and_lane_members(client, role_management_headers):
    """Lane bindings (P93-S1, ADR 0249): groups and roles of a principal -
    roles also through a group - and the principals behind a group or role."""
    group = client.post(
        "/groups", json={"name": "Lane-Sachbearbeitung"}, headers=role_management_headers
    ).json()
    for principal in ("lena", "lars"):
        client.post(
            f"/groups/{group['id']}/members",
            json={"principal_id": principal},
            headers=role_management_headers,
        )
    role = client.post(
        "/roles",
        json={"name": "Lane-Freigabe", "permissions": ["x.approve"]},
        headers=role_management_headers,
    ).json()["role"]
    # The role directly for "frida" and through the group for its members.
    for principal_type, principal in (("user", "frida"), ("group", group["id"])):
        client.post(
            "/role-assignments",
            json={
                "principal_type": principal_type,
                "principal_id": principal,
                "role_id": role["id"],
                "resource_id": ROOT_RESOURCE_ID,
            },
        )

    lena = client.get("/principals/lena/memberships").json()
    assert [g["name"] for g in lena["groups"]] == ["Lane-Sachbearbeitung"]
    assert "Lane-Freigabe" in lena["roles"]
    frida = client.get("/principals/frida/memberships").json()
    assert frida["groups"] == [] and "Lane-Freigabe" in frida["roles"]
    assert "Lane-Freigabe" not in client.get("/principals/nobody/memberships").json()["roles"]

    by_group = client.get("/lane-members", params={"group": "Lane-Sachbearbeitung"}).json()
    assert by_group["principal_ids"] == ["lars", "lena"]
    by_role = client.get("/lane-members", params={"role": "Lane-Freigabe"}).json()
    assert by_role["principal_ids"] == ["frida", "lars", "lena"]

    assert client.get("/lane-members", params={"group": "gibt-es-nicht"}).status_code == 404
    assert client.get("/lane-members").status_code == 422
    assert client.get("/lane-members", params={"group": "a", "role": "b"}).status_code == 422


def test_delete_role_removes_it_with_its_assignments(client, role_management_headers):
    """P94-S3: roles can be removed (test runs had left ~2,600 behind); the
    assignments go with them and the principal loses the permission."""
    created = client.post(
        "/roles",
        json={"name": "loesch-test", "permissions": ["delete.test"]},
        headers=role_management_headers,
    ).json()["role"]
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "loesch-user",
            "role_id": created["id"],
            "resource_id": "root",
        },
    ).raise_for_status()
    check = {"principal_id": "loesch-user", "resource_id": "root", "permission": "delete.test"}
    assert client.get("/check", params=check).json()["allowed"] is True

    forbidden = client.delete(f"/roles/{created['id']}", headers={"X-DMS-Principal": "nobody"})
    assert forbidden.status_code == 403
    response = client.delete(f"/roles/{created['id']}", headers=role_management_headers)

    assert response.status_code == 204
    assert all(r["id"] != created["id"] for r in client.get("/roles").json())
    assert client.get("/check", params=check).json()["allowed"] is False
    again = client.delete(f"/roles/{created['id']}", headers=role_management_headers)
    assert again.status_code == 404


def test_seeded_roles_cannot_be_deleted(client, role_management_headers):
    roles = {r["name"]: r["id"] for r in client.get("/roles").json()}
    for name in ("domain-admin-users", "everyone"):
        response = client.delete(f"/roles/{roles[name]}", headers=role_management_headers)
        assert response.status_code == 409, name
        assert "nicht löschbar" in response.json()["detail"]


def test_delete_approval_config(client, role_management_headers):
    client.put(
        "/approval-config/delete.test.action",
        json={"requires_approval": True},
        headers=role_management_headers,
    ).raise_for_status()

    response = client.delete("/approval-config/delete.test.action", headers=role_management_headers)

    assert response.status_code == 204
    assert client.get("/approval-config/delete.test.action").json()["requires_approval"] is False
    missing = client.delete("/approval-config/delete.test.action", headers=role_management_headers)
    assert missing.status_code == 404


def test_a_warm_check_is_one_database_statement(client):
    """P95-S2b: with a valid cache entry and no active scope lock, `GET
    /check` reads once (plus the pool's pre-ping) - before, about a dozen
    round trips: BEGIN, generations, the row, two per ancestor level for
    scope locks, COMMIT."""
    from sqlalchemy import event

    params = {
        "principal_id": "hot-path",
        "resource_id": "root",
        "permission": "document.read",
        "access_type": "read",
    }
    assert client.get("/check", params=params).status_code == 200  # fills the cache

    statements: list[str] = []

    def on_execute(conn, cursor, statement, *_args):
        statements.append(statement)

    sync_engine = app.state.engine.sync_engine
    event.listen(sync_engine, "before_cursor_execute", on_execute)
    try:
        response = client.get("/check", params=params)
    finally:
        event.remove(sync_engine, "before_cursor_execute", on_execute)
    assert response.status_code == 200
    real = [s for s in statements if s.strip().lower() not in ("select 1", ";")]
    assert len(real) == 1, statements


# --- Permission explanation (P96-S2, ADR 0253 D1) ---------------------------


def _node(client, resource_id, parent_id, inherit=True):
    client.post(
        "/resources",
        json={"resource_id": resource_id, "parent_id": parent_id, "resource_type": "folder"},
    )
    if not inherit:
        client.patch(f"/resources/{resource_id}", json={"inherit": False})


def _role(client, headers, name, permissions):
    return client.post(
        "/roles", json={"name": name, "permissions": permissions}, headers=headers
    ).json()["role"]


def test_explain_shows_the_deciding_step_and_its_source(client, role_management_headers):
    """A group assignment two levels up grants; the explanation names the
    node, the group and the role - and agrees with `/check`."""
    _node(client, "ex-amt", ROOT_RESOURCE_ID)
    _node(client, "ex-akte", "ex-amt")
    reader = _role(client, role_management_headers, "ex-reader", ["folder.read"])
    group = client.post("/groups", json={"name": "Bauamt"}, headers=role_management_headers).json()
    client.post(
        f"/groups/{group['id']}/members",
        json={"principal_id": "frau-x"},
        headers=role_management_headers,
    )
    client.post(
        "/role-assignments",
        json={
            "principal_type": "group",
            "principal_id": group["id"],
            "role_id": reader["id"],
            "resource_id": "ex-amt",
        },
    )

    response = client.get(
        "/explain/frau-x/ex-akte",
        params={"permission": "folder.read", "access_type": "read"},
        headers=role_management_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["allowed"] is True
    assert "ex-amt" in body["reason"]
    assert body["groups"] == ["Bauamt"]
    assert [step["resource_id"] for step in body["steps"]][:2] == ["ex-akte", "ex-amt"]
    [granting] = [a for a in body["steps"][1]["assignments"] if a["grants_permission"]]
    assert granting == {
        "assignment_id": granting["assignment_id"],
        "source": "group:Bauamt",
        "role": "ex-reader",
        "grants_permission": True,
    }
    check = client.get(
        "/check",
        params={
            "principal_id": "frau-x",
            "resource_id": "ex-akte",
            "permission": "folder.read",
            "access_type": "read",
        },
    ).json()
    assert check["allowed"] is body["allowed"]


def test_explain_names_a_broken_inheritance(client, role_management_headers):
    _node(client, "ex2-amt", ROOT_RESOURCE_ID)
    reader = _role(client, role_management_headers, "ex2-reader", ["folder.read"])
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "herr-y",
            "role_id": reader["id"],
            "resource_id": "ex2-amt",
        },
    )
    _node(client, "ex2-geheim", "ex2-amt", inherit=False)

    body = client.get(
        "/explain/herr-y/ex2-geheim",
        params={"permission": "folder.read", "access_type": "read"},
        headers=role_management_headers,
    ).json()

    assert body["allowed"] is False
    assert "inherit=false" in body["reason"]
    assert [step["resource_id"] for step in body["steps"]] == ["ex2-geheim"]


def test_explain_reports_a_blocking_scope_lock_and_needs_the_admin_capability(
    client, role_management_headers
):
    _node(client, "ex3-amt", ROOT_RESOURCE_ID)
    writer = _role(client, role_management_headers, "ex3-writer", ["folder.write"])
    client.post(
        "/role-assignments",
        json={
            "principal_type": "user",
            "principal_id": "dora",
            "role_id": writer["id"],
            "resource_id": "ex3-amt",
        },
    )
    client.post(
        "/scope-locks",
        json={"resource_id": "ex3-amt", "locked_by": "admin", "reason": "Prüfung"},
        headers=role_management_headers,
    )

    params = {"permission": "folder.write", "access_type": "write"}
    body = client.get(
        "/explain/dora/ex3-amt", params=params, headers=role_management_headers
    ).json()
    assert body["allowed"] is False
    assert "Bereichssperre" in body["reason"]
    assert body["steps"][0]["active_locks"][0]["reason"] == "Prüfung"

    assert client.get("/explain/dora/ex3-amt", params=params).status_code == 401
    forbidden = client.get(
        "/explain/dora/ex3-amt", params=params, headers={"X-DMS-Principal": "dora"}
    )
    assert forbidden.status_code == 403


def test_explain_for_an_unregistered_resource(client, role_management_headers):
    body = client.get(
        "/explain/someone/no-such-node",
        params={"permission": "document.read", "access_type": "read"},
        headers=role_management_headers,
    ).json()
    assert body["allowed"] is False
    assert "nicht registriert" in body["reason"]
