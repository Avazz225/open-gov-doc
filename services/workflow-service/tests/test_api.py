import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from dms_eventbus_client import Event
from fastapi.testclient import TestClient
from workflow_service import main
from workflow_service.main import app

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "workflow-service-tests"}) as c:
        yield c


def _create_delegation(
    *,
    deputy_principal_id: str,
    delegator_principal_id: str,
    process_definition_id: int | None = None,
) -> dict:
    """Stellvertretung bei Abwesenheit (4.4a, P14-S11) - echter Aufruf gegen
    den laufenden permission-service (kein Mocking, gleiches Prinzip wie
    `_grant_config_admin_permission` in conftest.py)."""
    now = datetime.now(UTC)
    body = {
        "deputy_principal_id": deputy_principal_id,
        "starts_at": (now - timedelta(hours=1)).isoformat(),
        "ends_at": (now + timedelta(days=1)).isoformat(),
    }
    if process_definition_id is not None:
        body["scope_process_definition_ids"] = [process_definition_id]
    response = httpx.post(
        f"{PERMISSION_SERVICE_URL}/delegations",
        json=body,
        headers={"X-DMS-Principal": delegator_principal_id},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def _upload_definition(
    client, xml: str, *, name: str, headers: dict[str, str], process_id: str | None = None
):
    data = {"name": name}
    if process_id is not None:
        data["process_id"] = process_id
    files = {"bpmn_xml": ("process.bpmn", xml, "application/xml")}
    return client.post("/process-definitions", data=data, files=files, headers=headers)


def _delete_definition(client, definition_id: int, headers: dict[str, str]):
    return client.delete(f"/process-definitions/{definition_id}", headers=headers)


def _upload_dmn(client, xml: str, *, name: str, headers: dict[str, str]):
    data = {"name": name}
    files = {"dmn_xml": ("decision.dmn", xml, "application/xml")}
    return client.post("/dmn-definitions", data=data, files=files, headers=headers)


def _delete_dmn(client, dmn_definition_id: int, headers: dict[str, str]):
    return client.delete(f"/dmn-definitions/{dmn_definition_id}", headers=headers)


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "workflow-service"


def test_create_process_definition_without_permission_is_forbidden(client, manual_task_bpmn):
    response = _upload_definition(client, manual_task_bpmn, name="Approval", headers={})
    assert response.status_code == 403


def test_create_and_get_process_definition(client, manual_task_bpmn, admin_headers):
    create_response = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    )
    assert create_response.status_code == 201
    definition_id = create_response.json()["id"]
    assert create_response.json()["bpmn_process_id"] == "Process_cozt5fu"

    get_response = client.get(f"/process-definitions/{definition_id}")
    assert get_response.status_code == 200
    assert "bpmn:definitions" in get_response.json()["bpmn_xml"]


def test_create_process_definition_with_approval_required_defers_creation(
    client, manual_task_bpmn, admin_headers
):
    """Post-Roadmap Phase 21 Session 4 (ADR 0087) - mit aktivierter
    Genehmigungspflicht wird NICHT sofort angelegt, echte Integration gegen
    den lokal laufenden permission-service (kein Mocking), gleiches Muster
    wie config-service's `test_import_with_approval_required_defers_execution`.
    Der Erfolgsfall (Default, keine Genehmigungspflicht konfiguriert) bleibt
    unverändert `201` + `ProcessDefinitionOut` - siehe die zahlreichen
    anderen Tests in dieser Datei, die `_upload_definition(...).json()["id"]`
    unverändert weiterverwenden."""
    httpx.put(
        f"{PERMISSION_SERVICE_URL}/approval-config/workflow.process_definition.import",
        json={"requires_approval": True},
    )
    try:
        name = f"Approval-Pending-{uuid.uuid4().hex[:8]}"
        response = _upload_definition(client, manual_task_bpmn, name=name, headers=admin_headers)
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "pending_approval"
        assert body["result"] is None
        assert body["approval_request_id"] is not None

        # Keine sofortige Anlage - die Prozessfamilie existiert (noch) nicht.
        # Die tatsächliche Anwendung folgt asynchron über consumer.py, sobald
        # das Approval-Event eintrifft (siehe test_consumer.py).
        list_response = client.get("/process-definitions", params={"name": name})
        assert list_response.json() == []
    finally:
        httpx.put(
            f"{PERMISSION_SERVICE_URL}/approval-config/workflow.process_definition.import",
            json={"requires_approval": False},
        )


def test_create_process_definition_with_existing_name_creates_next_version(
    client, manual_task_bpmn, admin_headers
):
    first = _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    second = _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["version"] == 1
    assert second.json()["version"] == 2


def test_list_process_definitions_returns_only_latest_version_by_default(
    client, manual_task_bpmn, admin_headers
):
    _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    response = client.get("/process-definitions")
    [approval] = [d for d in response.json() if d["name"] == "Approval"]
    assert approval["version"] == 2


def test_list_process_definitions_with_name_filter_returns_full_history(
    client, manual_task_bpmn, admin_headers
):
    _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    response = client.get("/process-definitions", params={"name": "Approval"})
    assert [d["version"] for d in response.json()] == [2, 1]


def test_create_process_definition_invalid_bpmn_returns_422(client, admin_headers):
    response = client.post(
        "/process-definitions",
        data={"name": "Kaputt"},
        files={"bpmn_xml": ("process.bpmn", "not valid xml", "application/xml")},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_get_unknown_process_definition_returns_404(client):
    response = client.get("/process-definitions/999999")
    assert response.status_code == 404


def test_list_process_definitions(client, manual_task_bpmn, admin_headers):
    _upload_definition(client, manual_task_bpmn, name="Approval", headers=admin_headers)
    response = client.get("/process-definitions")
    assert response.status_code == 200
    assert any(d["name"] == "Approval" for d in response.json())


def test_delete_process_definition_without_permission_is_forbidden(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]

    response = _delete_definition(client, definition_id, headers={})

    assert response.status_code == 403


def test_delete_process_definition_with_instance_returns_409(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    client.post(f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"})
    response = _delete_definition(client, definition_id, headers=admin_headers)
    assert response.status_code == 409


def test_delete_process_definition_without_instances_succeeds(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    response = _delete_definition(client, definition_id, headers=admin_headers)
    assert response.status_code == 204
    assert client.get(f"/process-definitions/{definition_id}").status_code == 404


def test_create_dmn_definition_without_permission_is_forbidden(client, approval_level_dmn):
    response = _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers={})
    assert response.status_code == 403


def test_create_and_get_dmn_definition(client, approval_level_dmn, admin_headers):
    create_response = _upload_dmn(
        client, approval_level_dmn, name="Freigabestufe", headers=admin_headers
    )
    assert create_response.status_code == 201
    assert create_response.json()["decision_id"] == "approval-level"
    dmn_definition_id = create_response.json()["id"]

    get_response = client.get(f"/dmn-definitions/{dmn_definition_id}")
    assert get_response.status_code == 200
    assert "definitions" in get_response.json()["dmn_xml"]


def test_create_dmn_definition_with_existing_name_creates_next_version(
    client, approval_level_dmn, admin_headers
):
    first = _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    second = _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    assert first.json()["version"] == 1
    assert second.json()["version"] == 2


def test_create_dmn_definition_invalid_dmn_returns_422(client, admin_headers):
    response = client.post(
        "/dmn-definitions",
        data={"name": "Kaputt"},
        files={"dmn_xml": ("decision.dmn", "not valid xml", "application/xml")},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_create_dmn_definition_duplicate_decision_id_returns_409(
    client, approval_level_dmn, admin_headers
):
    _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    response = _upload_dmn(client, approval_level_dmn, name="Andere Familie", headers=admin_headers)
    assert response.status_code == 409


def test_get_unknown_dmn_definition_returns_404(client):
    response = client.get("/dmn-definitions/999999")
    assert response.status_code == 404


def test_list_dmn_definitions_returns_only_latest_version_by_default(
    client, approval_level_dmn, admin_headers
):
    _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    response = client.get("/dmn-definitions")
    [freigabestufe] = [d for d in response.json() if d["name"] == "Freigabestufe"]
    assert freigabestufe["version"] == 2


def test_delete_dmn_definition_without_permission_is_forbidden(
    client, approval_level_dmn, admin_headers
):
    dmn_definition_id = _upload_dmn(
        client, approval_level_dmn, name="Freigabestufe", headers=admin_headers
    ).json()["id"]
    response = _delete_dmn(client, dmn_definition_id, headers={})
    assert response.status_code == 403


def test_delete_dmn_definition_succeeds(client, approval_level_dmn, admin_headers):
    dmn_definition_id = _upload_dmn(
        client, approval_level_dmn, name="Freigabestufe", headers=admin_headers
    ).json()["id"]
    response = _delete_dmn(client, dmn_definition_id, headers=admin_headers)
    assert response.status_code == 204
    assert client.get(f"/dmn-definitions/{dmn_definition_id}").status_code == 404


def test_business_rule_task_process_definition_evaluates_dmn_end_to_end(
    client, business_rule_task_bpmn, approval_level_dmn, admin_headers
):
    """Ende-zu-Ende über die HTTP-API (P14-S4): DMN hochladen, referenzierende
    Prozessdefinition hochladen, Instanz starten - die Freigabestufe landet in
    den Task-Daten des abgeschlossenen Business Rule Task."""
    _upload_dmn(client, approval_level_dmn, name="Freigabestufe", headers=admin_headers)
    definition_id = _upload_definition(
        client, business_rule_task_bpmn, name="Freigabe-Workflow", headers=admin_headers
    ).json()["id"]

    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"amount": 1500}},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "completed"


def test_create_business_calendar_without_permission_is_forbidden(client):
    response = client.post(
        "/business-calendars", json={"name": "de-national", "non_working_dates": []}
    )
    assert response.status_code == 403


def test_create_and_get_business_calendar(client, admin_headers):
    create_response = client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": ["2026-12-25"], "is_default": False},
        headers=admin_headers,
    )
    assert create_response.status_code == 201
    calendar_id = create_response.json()["id"]
    assert create_response.json()["non_working_dates"] == ["2026-12-25"]

    get_response = client.get(f"/business-calendars/{calendar_id}")
    assert get_response.status_code == 200
    assert get_response.json()["name"] == "de-national"


def test_create_business_calendar_duplicate_name_returns_409(client, admin_headers):
    client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    )
    response = client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_create_business_calendar_invalid_date_returns_422(client, admin_headers):
    response = client.post(
        "/business-calendars",
        json={"name": "broken", "non_working_dates": ["not-a-date"]},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_get_unknown_business_calendar_returns_404(client):
    response = client.get("/business-calendars/999999")
    assert response.status_code == 404


def test_list_business_calendars(client, admin_headers):
    client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    )
    response = client.get("/business-calendars")
    assert response.status_code == 200
    assert any(c["name"] == "de-national" for c in response.json())


def test_update_business_calendar_without_permission_is_forbidden(client, admin_headers):
    calendar_id = client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    ).json()["id"]
    response = client.put(
        f"/business-calendars/{calendar_id}",
        json={"name": "de-national", "non_working_dates": []},
    )
    assert response.status_code == 403


def test_update_business_calendar_changes_fields(client, admin_headers):
    calendar_id = client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    ).json()["id"]
    response = client.put(
        f"/business-calendars/{calendar_id}",
        json={"name": "de-national", "non_working_dates": ["2026-12-25"], "is_default": True},
        headers=admin_headers,
    )
    assert response.status_code == 200
    assert response.json()["non_working_dates"] == ["2026-12-25"]
    assert response.json()["is_default"] is True


def test_delete_business_calendar_succeeds(client, admin_headers):
    calendar_id = client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    ).json()["id"]
    response = client.delete(f"/business-calendars/{calendar_id}", headers=admin_headers)
    assert response.status_code == 204
    assert client.get(f"/business-calendars/{calendar_id}").status_code == 404


def test_business_days_timer_process_respects_calendar_end_to_end(
    client, business_days_timer_bpmn, admin_headers
):
    """Ende-zu-Ende über die HTTP-API (P14-S5): ein hochgeladener Kalender wirkt
    sich über `business_days()` tatsächlich auf eine gestartete Instanz aus -
    hier mit `n=0` deterministisch fast sofort abschließend."""
    client.post(
        "/business-calendars",
        json={"name": "de-national", "non_working_dates": []},
        headers=admin_headers,
    )
    definition_id = _upload_definition(
        client, business_days_timer_bpmn, name="Wartezeit-Workflow", headers=admin_headers
    ).json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    )
    assert response.status_code == 201


def test_start_instance_with_manual_task_stays_running(client, manual_task_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "business_key": "doc-1"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "running"
    assert response.json()["business_key"] == "doc-1"


def test_start_instance_fully_automatic_completes_immediately(client, no_tasks_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, no_tasks_bpmn, name="NoTasks", headers=admin_headers
    ).json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "completed"


def test_start_instance_unknown_definition_returns_404(client):
    response = client.post("/process-definitions/999999/instances", json={"created_by": "alice"})
    assert response.status_code == 404


def test_start_instance_with_explicit_instance_id_uses_it(client, no_tasks_bpmn, admin_headers):
    """Caller-bestimmte Instanz-ID (P12-S2, gleiches Muster wie
    `federation-hub-service`s `handover_id`, ADR 0028) - wichtig für einen
    Aufrufer, der die ID bereits VOR dem Start persistieren will, um eine bei
    einem Fehlschlag trotzdem angelegte Instanz später wiederzufinden."""
    definition_id = _upload_definition(
        client, no_tasks_bpmn, name="NoTasksExplicitId", headers=admin_headers
    ).json()["id"]
    chosen_id = "caller-chosen-instance-id"
    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "instance_id": chosen_id},
    )
    assert response.status_code == 201
    assert response.json()["id"] == chosen_id
    assert client.get(f"/instances/{chosen_id}").status_code == 200


def test_start_instance_rejected_during_maintenance_mode(client, manual_task_bpmn, admin_headers):
    """Retrofit P6-S6 (4.8): Instanzstart bleibt für jeden authentifizierten
    Principal offen, respektiert aber die Notfallsperre - der Header wird vom
    Gateway gesetzt, hier direkt simuliert (kein Gateway im Testlauf)."""
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]

    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice"},
        headers={"X-DMS-Maintenance-Active": "true"},
    )

    assert response.status_code == 503


def test_get_ready_tasks_and_complete_it(client, manual_task_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()

    tasks_response = client.get(f"/instances/{instance['id']}/tasks")
    assert tasks_response.status_code == 200
    tasks = tasks_response.json()
    assert len(tasks) == 1
    assert tasks[0]["name"] == "manual"

    complete_response = client.post(
        f"/instances/{instance['id']}/tasks/{tasks[0]['id']}/complete",
        json={"completed_by": "bob", "data": {"decision": "approved"}},
    )
    assert complete_response.status_code == 200
    assert complete_response.json()["status"] == "completed"

    assert client.get(f"/instances/{instance['id']}/tasks").json() == []


def test_list_ready_tasks_spans_multiple_running_instances(client, manual_task_bpmn, admin_headers):
    """`GET /tasks` (8, P14-S2 Reviewer/Approval-UI) - bislang gab es nur die
    instanzgebundene `GET /instances/{id}/tasks`. Startet zwei Instanzen,
    schließt eine davon vollständig ab, und prüft, dass nur die noch offene
    Task der laufenden Instanz auftaucht, angereichert um `instance_id`."""
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="CrossInstanceTasks", headers=admin_headers
    ).json()["id"]
    running = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    to_complete = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task_to_complete = client.get(f"/instances/{to_complete['id']}/tasks").json()[0]
    client.post(
        f"/instances/{to_complete['id']}/tasks/{task_to_complete['id']}/complete",
        json={"completed_by": "bob"},
    )

    all_tasks = client.get("/tasks").json()
    matching = [t for t in all_tasks if t["instance_id"] == running["id"]]
    assert len(matching) == 1
    assert matching[0]["name"] == "manual"
    assert matching[0]["process_definition_id"] == definition_id
    assert matching[0]["business_key"] == running["business_key"]
    assert all(t["instance_id"] != to_complete["id"] for t in all_tasks)


def test_complete_task_rejected_during_maintenance_mode(client, manual_task_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    tasks = client.get(f"/instances/{instance['id']}/tasks").json()

    response = client.post(
        f"/instances/{instance['id']}/tasks/{tasks[0]['id']}/complete",
        json={"completed_by": "bob"},
        headers={"X-DMS-Maintenance-Active": "true"},
    )

    assert response.status_code == 503


def test_complete_unknown_task_returns_409(client, manual_task_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()

    response = client.post(
        f"/instances/{instance['id']}/tasks/does-not-exist/complete",
        json={"completed_by": "bob"},
    )
    assert response.status_code == 409


# --- Stellvertretung bei Abwesenheit (4.4a, P14-S11) -------------------------


def test_complete_task_on_behalf_of_without_principal_header_returns_401(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": "bob", "on_behalf_of_principal_id": "alice"},
        headers={"X-DMS-Principal": ""},
    )

    assert response.status_code == 401


def test_complete_task_on_behalf_of_without_active_delegation_returns_403(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]
    deputy = f"deputy-{uuid.uuid4().hex[:8]}"

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": "bob", "on_behalf_of_principal_id": "someone-without-delegation"},
        headers={"X-DMS-Principal": deputy},
    )

    assert response.status_code == 403


def test_complete_task_on_behalf_of_with_active_delegation_succeeds_and_annotates_event(
    client, manual_task_bpmn, admin_headers, monkeypatch
):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)

    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    delegator = f"delegator-{uuid.uuid4().hex[:8]}"
    deputy = f"deputy-{uuid.uuid4().hex[:8]}"
    _create_delegation(deputy_principal_id=deputy, delegator_principal_id=delegator)

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": deputy, "on_behalf_of_principal_id": delegator},
        headers={"X-DMS-Principal": deputy},
    )

    assert response.status_code == 200

    completed_events = [e for e in published if e.event_type == "workflow.task.completed"]
    assert len(completed_events) == 1
    assert completed_events[0].actor == deputy
    assert completed_events[0].on_behalf_of == delegator


def test_complete_task_on_behalf_of_respects_process_definition_scope(
    client, manual_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    other_definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval2", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    delegator = f"delegator-{uuid.uuid4().hex[:8]}"
    deputy = f"deputy-{uuid.uuid4().hex[:8]}"
    # Delegation gilt nur für einen ANDEREN Prozess als den, dessen Aufgabe
    # hier abgeschlossen werden soll - muss trotz existierender Delegation
    # abgelehnt werden.
    _create_delegation(
        deputy_principal_id=deputy,
        delegator_principal_id=delegator,
        process_definition_id=other_definition_id,
    )

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": deputy, "on_behalf_of_principal_id": delegator},
        headers={"X-DMS-Principal": deputy},
    )

    assert response.status_code == 403


def test_complete_task_without_on_behalf_of_needs_no_principal_header(
    client, manual_task_bpmn, admin_headers
):
    """Regulärer, nicht delegierter Abschluss bleibt unverändert möglich
    ohne X-DMS-Principal-Header - `completed_by` bleibt wie bisher ein
    ungeprüftes Freitextfeld, siehe main.py._require_delegation_if_on_behalf_of."""
    definition_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()
    task = client.get(f"/instances/{instance['id']}/tasks").json()[0]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task['id']}/complete",
        json={"completed_by": "bob"},
    )

    assert response.status_code == 200


def test_get_unknown_instance_returns_404(client):
    response = client.get("/instances/does-not-exist")
    assert response.status_code == 404


def test_get_ready_tasks_surfaces_signature_task_extensions(
    client, signature_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": "doc-1"}},
    ).json()

    tasks = client.get(f"/instances/{instance['id']}/tasks").json()
    assert len(tasks) == 1
    assert tasks[0]["extensions"] == {"taskType": "signature", "requiredLevel": "aes"}


def test_complete_signature_task_without_signature_id_returns_400(
    client, signature_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": "doc-1"}},
    ).json()
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/complete", json={"completed_by": "bob"}
    )
    assert response.status_code == 400


def test_complete_signature_task_with_unknown_signature_id_returns_400(
    client, signature_task_bpmn, admin_headers
):
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": "doc-1"}},
    ).json()
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/complete",
        json={"completed_by": "bob", "signature_id": "999999"},
    )
    assert response.status_code == 400


def test_complete_signature_task_with_mismatched_document_returns_400(
    client, signature_task_bpmn, admin_headers, real_signature
):
    document_id, signature_id, _level = real_signature
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": "ein-anderes-dokument"}},
    ).json()
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/complete",
        json={"completed_by": "bob", "signature_id": str(signature_id)},
    )
    assert response.status_code == 400
    assert document_id != "ein-anderes-dokument"


def test_complete_signature_task_with_insufficient_level_returns_400(
    client, signature_task_bpmn, admin_headers, real_ses_signature
):
    document_id, signature_id, level = real_ses_signature
    assert level == "ses"
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": document_id}},
    ).json()
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/complete",
        json={"completed_by": "bob", "signature_id": str(signature_id)},
    )
    assert response.status_code == 400


def test_complete_signature_task_with_valid_signature_succeeds(
    client, signature_task_bpmn, admin_headers, real_signature
):
    document_id, signature_id, level = real_signature
    assert level == "aes"
    definition_id = _upload_definition(
        client, signature_task_bpmn, name="Vertragsunterschrift", headers=admin_headers
    ).json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"document_id": document_id}},
    ).json()
    task_id = client.get(f"/instances/{instance['id']}/tasks").json()[0]["id"]

    response = client.post(
        f"/instances/{instance['id']}/tasks/{task_id}/complete",
        json={"completed_by": "bob", "signature_id": str(signature_id)},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "completed"


def test_list_instances_filters_by_status(client, manual_task_bpmn, no_tasks_bpmn, admin_headers):
    running_id = _upload_definition(
        client, manual_task_bpmn, name="Approval", headers=admin_headers
    ).json()["id"]
    completed_id = _upload_definition(
        client, no_tasks_bpmn, name="NoTasks", headers=admin_headers
    ).json()["id"]
    client.post(f"/process-definitions/{running_id}/instances", json={"created_by": "alice"})
    client.post(f"/process-definitions/{completed_id}/instances", json={"created_by": "alice"})

    running = client.get("/instances", params={"status": "running"}).json()
    completed = client.get("/instances", params={"status": "completed"}).json()
    assert len(running) == 1
    assert len(completed) == 1


def test_instance_with_connector_service_task_completes_via_stub(
    client, connector_service_task_bpmn, admin_headers, monkeypatch
):
    """Ende-zu-Ende (7.1, P12-S2): ein echter `POST /process-definitions/{id}/instances`
    treibt einen `connector_call`-Service-Task, der synchron gegen einen In-Prozess-
    HTTP-Stub aufgerufen wird (kein Mocking der eigenen Geschäftslogik, nur des
    ausgehenden Netzwerktransports - gleiches Prinzip wie `federation-hub-service`s
    Tests, dort mit `AsyncClient`/`ASGITransport`, hier synchron mit `MockTransport`)."""

    def stub(request: httpx.Request) -> httpx.Response:
        assert request.url == "http://connector-stub.invalid/step"
        return httpx.Response(200, json={"result": "ok"})

    monkeypatch.setattr(
        main, "_connector_http_client", httpx.Client(transport=httpx.MockTransport(stub))
    )

    definition_id = _upload_definition(
        client, connector_service_task_bpmn, name="ConnectorCall", headers=admin_headers
    ).json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "completed"


def test_connector_service_task_service_url_supports_process_data_templating(
    client, connector_service_task_templated_bpmn, admin_headers, monkeypatch
):
    """`serviceUrl` kann `{platzhalter}` aus den aktuellen Prozessdaten referenzieren
    (P12-S2, Grundlage für migration-service's pro-Transfer unterschiedliche
    Schritt-Endpunkte) - hier `{transfer_id}`, gesetzt über `initial_data`."""
    called_urls = []

    def stub(request: httpx.Request) -> httpx.Response:
        called_urls.append(str(request.url))
        return httpx.Response(200, json={"result": "ok"})

    monkeypatch.setattr(
        main, "_connector_http_client", httpx.Client(transport=httpx.MockTransport(stub))
    )

    definition_id = _upload_definition(
        client,
        connector_service_task_templated_bpmn,
        name="ConnectorCallTemplated",
        headers=admin_headers,
    ).json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "initial_data": {"transfer_id": "abc-123"}},
    )
    assert response.status_code == 201
    assert called_urls == ["http://connector-stub.invalid/transfers/abc-123/steps/lock"]


def test_retry_instance_resumes_after_a_failed_connector_call(
    connector_service_task_bpmn, admin_headers, monkeypatch
):
    # Eigener `TestClient` mit `raise_server_exceptions=False` statt der geteilten
    # `client`-Fixture: Starlettes Default-Verhalten reicht eine unbehandelte
    # Exception zu Debug-Zwecken direkt an den Aufrufer durch, statt sie (wie ein
    # echter uvicorn-Prozess) als reguläre 500-Antwort zurückzugeben - genau diese
    # reale 500-Antwort will dieser Test aber tatsächlich sehen und weiterverarbeiten.
    with TestClient(
        app, raise_server_exceptions=False, headers={"X-DMS-Principal": "workflow-service-tests"}
    ) as client:
        attempts = {"count": 0}

        def stub(request: httpx.Request) -> httpx.Response:
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise httpx.ConnectError("Ziel nicht erreichbar", request=request)
            return httpx.Response(200, json={"result": "ok"})

        monkeypatch.setattr(
            main, "_connector_http_client", httpx.Client(transport=httpx.MockTransport(stub))
        )

        definition_id = _upload_definition(
            client, connector_service_task_bpmn, name="ConnectorCallRetry", headers=admin_headers
        ).json()["id"]
        start_response = client.post(
            f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
        )
        assert start_response.status_code == 500

        instance_id = client.get("/instances").json()[0]["id"]
        assert client.get(f"/instances/{instance_id}").json()["status"] == "running"

        retry_response = client.post(f"/instances/{instance_id}/retry")
    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "completed"
    assert attempts["count"] == 2


def test_retry_instance_on_completed_instance_returns_409(client, no_tasks_bpmn, admin_headers):
    definition_id = _upload_definition(
        client, no_tasks_bpmn, name="NoTasksRetry", headers=admin_headers
    ).json()["id"]
    instance_id = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()["id"]

    response = client.post(f"/instances/{instance_id}/retry")
    assert response.status_code == 409


def test_retry_unknown_instance_returns_404(client):
    response = client.post("/instances/does-not-exist/retry")
    assert response.status_code == 404
