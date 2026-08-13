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


def test_create_role(client, role_management_headers):
    response = client.post(
        "/roles",
        json={"name": "Viewer", "description": "", "permissions": ["read"]},
        headers=role_management_headers,
    )
    assert response.status_code == 200
    assert response.json()["permissions"] == ["read"]


def test_update_role_changes_description_and_permissions(client, role_management_headers):
    created = client.post(
        "/roles",
        json={"name": "Editor", "description": "alt", "permissions": ["read"]},
        headers=role_management_headers,
    ).json()

    response = client.put(
        f"/roles/{created['id']}",
        json={"description": "neu", "permissions": ["read", "write"]},
        headers=role_management_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Editor"
    assert body["description"] == "neu"
    assert body["permissions"] == ["read", "write"]


def test_update_unknown_role_returns_404(client, role_management_headers):
    response = client.put(
        "/roles/999999",
        json={"description": "x", "permissions": []},
        headers=role_management_headers,
    )
    assert response.status_code == 404


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
    ).json()
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
    ).json()
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
    ).json()
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
    response = client.post(
        "/check/batch",
        json={
            "principal_id": "no-such-principal",
            "permission": "document.read",
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
    ).json()
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


def test_list_role_assignments_returns_all(client, role_management_headers):
    role = client.post(
        "/roles",
        json={"name": "Listener", "permissions": ["read"]},
        headers=role_management_headers,
    ).json()
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
    ).json()
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
    response = client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": ""},
    )
    assert response.status_code == 403


def test_scope_lock_with_unknown_resource_returns_404(client, role_management_headers):
    response = client.post(
        "/scope-locks",
        json={"resource_id": "does-not-exist", "locked_by": "admin"},
    )
    assert response.status_code == 404


def test_scope_lock_blocks_write_check_with_reason(client, role_management_headers):
    created = client.post(
        "/scope-locks",
        json={
            "resource_id": ROOT_RESOURCE_ID,
            "locked_by": "admin",
            "reason": "Revision läuft",
        },
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

    release = client.request("DELETE", f"/scope-locks/{lock['id']}", json={"released_by": "admin"})
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


def test_scope_lock_with_blocks_read_also_blocks_read_access(client, role_management_headers):
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


def test_scope_lock_bypass_capability_overrides_block(client, role_management_headers):
    client.post("/scope-locks", json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"})
    role = client.post(
        "/roles",
        json={"name": "ScopeLockBypasser", "permissions": ["scope_lock.bypass", "write"]},
        headers=role_management_headers,
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


def test_list_scope_locks_endpoint(client, role_management_headers):
    client.post("/scope-locks", json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"})

    response = client.get("/scope-locks", params={"resource_id": ROOT_RESOURCE_ID})

    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_effective_scope_locks_endpoint(client, role_management_headers):
    client.post("/scope-locks", json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"})

    response = client.get(f"/scope-locks/effective/{ROOT_RESOURCE_ID}")

    assert response.status_code == 200
    assert len(response.json()) >= 1


def test_release_unknown_scope_lock_returns_404(client, role_management_headers):
    response = client.request("DELETE", "/scope-locks/999999", json={"released_by": "admin"})
    assert response.status_code == 404


def test_approval_config_defaults_to_false_and_is_settable(client):
    default = client.get("/approval-config/document.force_unlock").json()
    assert default["requires_approval"] is False

    updated = client.put("/approval-config/document.force_unlock", json={"requires_approval": True})
    assert updated.status_code == 200
    assert updated.json()["requires_approval"] is True

    listed = client.get("/approval-config").json()
    assert any(
        c["action_type"] == "document.force_unlock" and c["requires_approval"] is True
        for c in listed
    )


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
    )
    assert created.status_code == 201
    request_id = created.json()["id"]

    client.post(f"/approval-requests/{request_id}/approve", json={"approved_by": "bob"})

    requested = [e for e in published if e.event_type == "permission.approval.requested"]
    approved = [e for e in published if e.event_type == "permission.approval.approved"]
    assert requested[0].actor == "alice"
    assert approved[0].actor == "bob"


def test_approval_request_lifecycle_via_api(client):
    created = client.post(
        "/approval-requests",
        json={"action_type": "document.force_unlock", "initiated_by": "alice", "payload": {"x": 1}},
    )
    assert created.status_code == 201
    request = created.json()
    assert request["status"] == "pending"

    fetched = client.get(f"/approval-requests/{request['id']}")
    assert fetched.status_code == 200

    same_person = client.post(
        f"/approval-requests/{request['id']}/approve", json={"approved_by": "alice"}
    )
    assert same_person.status_code == 403

    approved = client.post(
        f"/approval-requests/{request['id']}/approve", json={"approved_by": "bob"}
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["approved_by"] == "bob"

    double_decision = client.post(
        f"/approval-requests/{request['id']}/approve", json={"approved_by": "carol"}
    )
    assert double_decision.status_code == 409


def test_approval_request_reject_allows_initiator(client):
    request = client.post(
        "/approval-requests",
        json={"action_type": "document.force_unlock", "initiated_by": "alice", "payload": {}},
    ).json()

    rejected = client.post(
        f"/approval-requests/{request['id']}/reject",
        json={"rejected_by": "alice", "reason": "Doch nicht nötig"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"


def test_get_unknown_approval_request_returns_404(client):
    response = client.get("/approval-requests/does-not-exist")
    assert response.status_code == 404


def test_create_scope_lock_with_approval_required_defers_creation(client, role_management_headers):
    client.put("/approval-config/permission.scope_lock.create", json={"requires_approval": True})

    response = client.post(
        "/scope-locks",
        json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin", "reason": "Migration"},
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
        "/scope-locks", json={"resource_id": ROOT_RESOURCE_ID, "locked_by": "admin"}
    ).json()
    lock_id = created["scope_lock"]["id"]

    client.put("/approval-config/permission.scope_lock.release", json={"requires_approval": True})

    response = client.request("DELETE", f"/scope-locks/{lock_id}", json={"released_by": "admin"})

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
    ).json()
    client.put(
        "/approval-config/permission.role_assignment.create", json={"requires_approval": True}
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
    ).json()
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
    ).json()

    response = client.post(
        f"/approval-requests/{request['id']}/approve", json={"approved_by": "bob"}
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
    ).json()

    response = client.post(
        f"/approval-requests/{request['id']}/approve", json={"approved_by": "bob"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_get_maintenance_mode_defaults_to_inactive(client):
    response = client.get("/maintenance-mode")

    assert response.status_code == 200
    assert response.json()["active"] is False


def test_trigger_maintenance_mode_without_permission_is_forbidden(client):
    response = client.post(
        "/maintenance-mode/trigger", json={"triggered_by": "alice", "reason": "Verdacht"}
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
        "/maintenance-mode/trigger", json={"triggered_by": "alice", "reason": "Verdacht"}
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
    )

    response = client.post(
        "/maintenance-mode/trigger", json={"triggered_by": "alice", "reason": "Verdacht"}
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
    )
    request = client.post(
        "/maintenance-mode/trigger", json={"triggered_by": "alice", "reason": "Verdacht"}
    ).json()

    approval = client.post(
        f"/approval-requests/{request['approval_request_id']}/approve",
        json={"approved_by": "bob"},
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
    client.post("/maintenance-mode/trigger", json={"triggered_by": "alice", "reason": None})

    response = client.post("/maintenance-mode/lift", json={"lifted_by": "alice"})

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

    response = client.get("/delegations", params={"deputy_principal_id": "bob"})

    assert response.status_code == 200
    assert len(response.json()) == 1
    assert response.json()[0]["deputy_principal_id"] == "bob"


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


def test_revoke_delegation_requires_creator_or_admin_role(client):
    created = client.post(
        "/delegations",
        json={"deputy_principal_id": "bob", **_delegation_window()},
        headers={"X-DMS-Principal": "alice"},
    ).json()

    forbidden = client.delete(
        f"/delegations/{created['id']}", headers={"X-DMS-Principal": "someone-else"}
    )
    assert forbidden.status_code == 403

    admin = client.delete(
        f"/delegations/{created['id']}",
        headers={"X-DMS-Principal": "an-admin", "X-DMS-Roles": "dms-admin"},
    )
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
