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
        app.state.permission_client = AsyncMock()
        app.state.permission_client.has_permission.return_value = True
        app.state.permission_client.check_batch.return_value = {}
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
