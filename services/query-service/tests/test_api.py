from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from query_service.main import app
from query_service.parser import load_parser_plugin


@pytest.fixture
def client():
    """Externe Clients (audit-/document-/permission-/auth-service) durch
    AsyncMock ersetzt - identisches Muster wie reporting-service's
    `test_api.py`-Fixture. `app.state.event_bus` bleibt der echte, in der
    Lifespan verbundene `NatsEventBusClient` (kein Mock) - die Selbst-
    Auditierung (`query.executed`) laeuft real gegen NATS, exakt wie bei
    reporting-service."""
    with TestClient(app) as c:
        app.state.audit_client = AsyncMock()
        app.state.audit_client.list_events.return_value = []
        app.state.document_client = AsyncMock()
        app.state.document_client.get_document.return_value = None
        app.state.object_type_client = AsyncMock()
        app.state.object_type_client.get_object_type.return_value = None
        app.state.permission_client = AsyncMock()
        app.state.permission_client.has_permission.return_value = True
        app.state.permission_client.check_batch.return_value = {}
        app.state.permission_client.requires_approval.return_value = False
        app.state.auth_client = AsyncMock()
        app.state.auth_client.get_active_superuser.return_value = (False, None)
        app.state.parser_plugin = None
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "query-service"


def test_query_events_requires_principal_header(client):
    response = client.get("/query/events")
    assert response.status_code == 403


def test_query_events_requires_query_console_role(client):
    app.state.permission_client.has_permission.return_value = False
    response = client.get("/query/events", headers={"x-dms-principal": "alice"})
    assert response.status_code == 403


def test_query_events_returns_empty_result_with_role(client):
    response = client.get("/query/events", headers={"x-dms-principal": "alice"})
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "events": [],
        "total_before_filter": 0,
        "total_after_filter": 0,
        "superuser": False,
    }


def test_query_events_filters_result_by_permission(client):
    app.state.audit_client.list_events.return_value = [
        {
            "id": 1,
            "service_name": "document-service",
            "subject": "doc-1",
            "event_type": "document.viewed",
        },
        {
            "id": 2,
            "service_name": "document-service",
            "subject": "doc-2",
            "event_type": "document.viewed",
        },
    ]

    async def fake_get_document(document_id: str) -> dict:
        folder_id = "folder-a" if document_id == "doc-1" else "folder-b"
        return {"id": document_id, "folder_id": folder_id}

    app.state.document_client.get_document.side_effect = fake_get_document
    app.state.permission_client.check_batch.return_value = {"folder-a": True, "folder-b": False}

    response = client.get("/query/events", headers={"x-dms-principal": "alice"})
    assert response.status_code == 200
    body = response.json()
    assert body["total_before_filter"] == 2
    assert body["total_after_filter"] == 1
    assert [e["id"] for e in body["events"]] == [1]


def test_query_events_superuser_bypasses_role_gate_and_filtering(client):
    app.state.permission_client.has_permission.return_value = False
    app.state.auth_client.get_active_superuser.return_value = (True, "root-admin")
    app.state.audit_client.list_events.return_value = [
        {
            "id": 1,
            "service_name": "workflow-service",
            "subject": "instance-1",
            "event_type": "workflow.instance.completed",
        },
    ]

    response = client.get("/query/events", headers={"x-dms-principal": "root-admin"})
    assert response.status_code == 200
    body = response.json()
    assert body["superuser"] is True
    assert body["total_after_filter"] == 1


def test_query_events_only_the_active_superuser_bypasses_gate(client):
    """Wer nicht selbst der gerade aktive Superuser ist, bekommt trotz
    laufender Aktivierung keine Sonderrechte."""
    app.state.permission_client.has_permission.return_value = False
    app.state.auth_client.get_active_superuser.return_value = (True, "root-admin")

    response = client.get("/query/events", headers={"x-dms-principal": "someone-else"})
    assert response.status_code == 403


def test_query_text_returns_501_without_configured_plugin(client):
    response = client.post(
        "/query",
        json={"query_text": "SELECT * FROM events"},
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 501


def test_query_text_with_configured_plugin_parses_and_runs(client):
    app.state.parser_plugin = load_parser_plugin("fake_parser_plugin")
    app.state.audit_client.list_events.return_value = [
        {
            "id": 1,
            "service_name": "folder-service",
            "subject": "folder-a",
            "event_type": "folder.created",
        },
    ]
    app.state.permission_client.check_batch.return_value = {"folder-a": True}

    response = client.post(
        "/query",
        json={"query_text": "SELECT * FROM events WHERE subject = 'folder-a'"},
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_after_filter"] == 1


def test_query_text_returns_400_on_unparseable_text(client):
    app.state.parser_plugin = load_parser_plugin("fake_parser_plugin")

    response = client.post(
        "/query", json={"query_text": "garbage"}, headers={"x-dms-principal": "alice"}
    )
    assert response.status_code == 400


def test_query_text_returns_400_on_unknown_table(client):
    app.state.parser_plugin = load_parser_plugin("fake_parser_plugin")

    response = client.post(
        "/query", json={"query_text": "SELECT * FROM users"}, headers={"x-dms-principal": "alice"}
    )
    assert response.status_code == 400


def test_query_text_requires_query_console_role(client):
    app.state.parser_plugin = load_parser_plugin("fake_parser_plugin")
    app.state.permission_client.has_permission.return_value = False

    response = client.post(
        "/query",
        json={"query_text": "SELECT * FROM events"},
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 403


def test_activate_manipulation_mode_requires_role(client):
    app.state.permission_client.has_permission.return_value = False
    response = client.post(
        "/manipulation-mode/activate",
        json={"duration_minutes": 10},
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 403


def test_activate_and_deactivate_manipulation_mode(client):
    response = client.post(
        "/manipulation-mode/activate",
        json={"duration_minutes": 10},
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["active"] is True
    assert body["activated_by"] == "alice"

    status_response = client.get("/manipulation-mode/status")
    assert status_response.json()["active"] is True

    deactivate_response = client.post(
        "/manipulation-mode/deactivate", headers={"x-dms-principal": "alice"}
    )
    assert deactivate_response.json()["active"] is False
    assert client.get("/manipulation-mode/status").json()["active"] is False


def test_dry_run_requires_active_manipulation_mode(client):
    response = client.post(
        "/manipulate/dry-run",
        json={"action_type": "document.attribute_reset", "params": {}},
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 403


def test_dry_run_unknown_action_returns_400(client):
    client.post(
        "/manipulation-mode/activate",
        json={"duration_minutes": 10},
        headers={"x-dms-principal": "alice"},
    )
    response = client.post(
        "/manipulate/dry-run",
        json={"action_type": "does.not.exist", "params": {}},
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 400


def test_dry_run_then_execute_non_critical_action_without_approval(client):
    client.post(
        "/manipulation-mode/activate",
        json={"duration_minutes": 10},
        headers={"x-dms-principal": "alice"},
    )
    app.state.document_client.get_document.return_value = {
        "id": "doc-1",
        "attributes": {"notiz": "alt"},
    }
    app.state.document_client.update_document.return_value = {
        "id": "doc-1",
        "attributes": {},
    }
    params = {"document_id": "doc-1", "attribute_key": "notiz"}

    dry_run = client.post(
        "/manipulate/dry-run",
        json={"action_type": "document.attribute_reset", "params": params},
        headers={"x-dms-principal": "alice"},
    )
    assert dry_run.status_code == 200
    dry_run_body = dry_run.json()
    assert dry_run_body["is_critical"] is False
    assert "notiz" in dry_run_body["preview"]

    execute = client.post(
        "/manipulate/execute",
        json={"dry_run_token": dry_run_body["dry_run_token"]},
        headers={"x-dms-principal": "alice"},
    )
    assert execute.status_code == 200
    assert execute.json() == {
        "status": "executed",
        "result": {"document_id": "doc-1", "attributes": {}},
        "approval_request_id": None,
    }


def test_execute_non_critical_action_requires_approval_when_configured(client):
    client.post(
        "/manipulation-mode/activate",
        json={"duration_minutes": 10},
        headers={"x-dms-principal": "alice"},
    )
    app.state.document_client.get_document.return_value = {
        "id": "doc-1",
        "attributes": {"notiz": "alt"},
    }
    app.state.permission_client.requires_approval.return_value = True
    app.state.permission_client.create_approval_request.return_value = {"id": "req-1"}
    params = {"document_id": "doc-1", "attribute_key": "notiz"}

    dry_run = client.post(
        "/manipulate/dry-run",
        json={"action_type": "document.attribute_reset", "params": params},
        headers={"x-dms-principal": "alice"},
    )
    execute = client.post(
        "/manipulate/execute",
        json={"dry_run_token": dry_run.json()["dry_run_token"]},
        headers={"x-dms-principal": "alice"},
    )
    assert execute.status_code == 200
    assert execute.json() == {
        "status": "pending_approval",
        "result": None,
        "approval_request_id": "req-1",
    }
    app.state.document_client.update_document.assert_not_called()


def test_critical_action_always_requires_approval_even_for_superuser(client):
    """Konzept 6.1 Punkt 4: die einzige Stelle, an der der aktivierte
    Superuser NICHT uneingeschraenkt agieren darf."""
    app.state.auth_client.get_active_superuser.return_value = (True, "root-admin")
    app.state.permission_client.get_role_assignment.return_value = {
        "id": 393,
        "principal_type": "user",
        "principal_id": "bob",
        "role_id": 5,
        "resource_id": "root",
    }
    app.state.permission_client.get_role.return_value = {"id": 5, "name": "reader"}
    app.state.permission_client.create_approval_request.return_value = {"id": "req-critical"}
    # requires_approval would say False - the action's own is_critical flag
    # must still force an approval request (hardcoded, not configurable).
    app.state.permission_client.requires_approval.return_value = False
    params = {"role_assignment_id": 393}

    dry_run = client.post(
        "/manipulate/dry-run",
        json={"action_type": "permission.role_assignment.delete", "params": params},
        headers={"x-dms-principal": "root-admin"},
    )
    assert dry_run.status_code == 200
    assert dry_run.json()["is_critical"] is True

    execute = client.post(
        "/manipulate/execute",
        json={"dry_run_token": dry_run.json()["dry_run_token"]},
        headers={"x-dms-principal": "root-admin"},
    )
    assert execute.status_code == 200
    assert execute.json()["status"] == "pending_approval"
    app.state.permission_client.delete_role_assignment.assert_not_called()


def test_superuser_bypasses_manipulation_mode_schutzschalter(client):
    app.state.auth_client.get_active_superuser.return_value = (True, "root-admin")
    app.state.document_client.get_document.return_value = {
        "id": "doc-1",
        "attributes": {"notiz": "alt"},
    }
    response = client.post(
        "/manipulate/dry-run",
        json={
            "action_type": "document.attribute_reset",
            "params": {"document_id": "doc-1", "attribute_key": "notiz"},
        },
        headers={"x-dms-principal": "root-admin"},
    )
    assert response.status_code == 200


def test_execute_rejects_invalid_dry_run_token(client):
    client.post(
        "/manipulation-mode/activate",
        json={"duration_minutes": 10},
        headers={"x-dms-principal": "alice"},
    )
    response = client.post(
        "/manipulate/execute",
        json={"dry_run_token": "garbage-token"},
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 400
