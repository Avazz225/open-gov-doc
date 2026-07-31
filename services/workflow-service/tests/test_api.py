import pytest
from fastapi.testclient import TestClient
from workflow_service.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def _upload_definition(client, xml: str, *, name: str, process_id: str | None = None):
    data = {"name": name}
    if process_id is not None:
        data["process_id"] = process_id
    files = {"bpmn_xml": ("process.bpmn", xml, "application/xml")}
    return client.post("/process-definitions", data=data, files=files)


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "workflow-service"


def test_create_and_get_process_definition(client, manual_task_bpmn):
    create_response = _upload_definition(client, manual_task_bpmn, name="Approval")
    assert create_response.status_code == 201
    definition_id = create_response.json()["id"]
    assert create_response.json()["bpmn_process_id"] == "Process_cozt5fu"

    get_response = client.get(f"/process-definitions/{definition_id}")
    assert get_response.status_code == 200
    assert "bpmn:definitions" in get_response.json()["bpmn_xml"]


def test_create_process_definition_duplicate_name_returns_409(client, manual_task_bpmn):
    _upload_definition(client, manual_task_bpmn, name="Approval")
    response = _upload_definition(client, manual_task_bpmn, name="Approval")
    assert response.status_code == 409


def test_create_process_definition_invalid_bpmn_returns_422(client):
    response = client.post(
        "/process-definitions",
        data={"name": "Kaputt"},
        files={"bpmn_xml": ("process.bpmn", "not valid xml", "application/xml")},
    )
    assert response.status_code == 422


def test_get_unknown_process_definition_returns_404(client):
    response = client.get("/process-definitions/999999")
    assert response.status_code == 404


def test_list_process_definitions(client, manual_task_bpmn):
    _upload_definition(client, manual_task_bpmn, name="Approval")
    response = client.get("/process-definitions")
    assert response.status_code == 200
    assert any(d["name"] == "Approval" for d in response.json())


def test_delete_process_definition_with_instance_returns_409(client, manual_task_bpmn):
    definition_id = _upload_definition(client, manual_task_bpmn, name="Approval").json()["id"]
    client.post(f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"})
    response = client.delete(f"/process-definitions/{definition_id}")
    assert response.status_code == 409


def test_delete_process_definition_without_instances_succeeds(client, manual_task_bpmn):
    definition_id = _upload_definition(client, manual_task_bpmn, name="Approval").json()["id"]
    response = client.delete(f"/process-definitions/{definition_id}")
    assert response.status_code == 204
    assert client.get(f"/process-definitions/{definition_id}").status_code == 404


def test_start_instance_with_manual_task_stays_running(client, manual_task_bpmn):
    definition_id = _upload_definition(client, manual_task_bpmn, name="Approval").json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "alice", "business_key": "doc-1"},
    )
    assert response.status_code == 201
    assert response.json()["status"] == "running"
    assert response.json()["business_key"] == "doc-1"


def test_start_instance_fully_automatic_completes_immediately(client, no_tasks_bpmn):
    definition_id = _upload_definition(client, no_tasks_bpmn, name="NoTasks").json()["id"]
    response = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    )
    assert response.status_code == 201
    assert response.json()["status"] == "completed"


def test_start_instance_unknown_definition_returns_404(client):
    response = client.post("/process-definitions/999999/instances", json={"created_by": "alice"})
    assert response.status_code == 404


def test_get_ready_tasks_and_complete_it(client, manual_task_bpmn):
    definition_id = _upload_definition(client, manual_task_bpmn, name="Approval").json()["id"]
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


def test_complete_unknown_task_returns_409(client, manual_task_bpmn):
    definition_id = _upload_definition(client, manual_task_bpmn, name="Approval").json()["id"]
    instance = client.post(
        f"/process-definitions/{definition_id}/instances", json={"created_by": "alice"}
    ).json()

    response = client.post(
        f"/instances/{instance['id']}/tasks/does-not-exist/complete",
        json={"completed_by": "bob"},
    )
    assert response.status_code == 409


def test_get_unknown_instance_returns_404(client):
    response = client.get("/instances/does-not-exist")
    assert response.status_code == 404


def test_list_instances_filters_by_status(client, manual_task_bpmn, no_tasks_bpmn):
    running_id = _upload_definition(client, manual_task_bpmn, name="Approval").json()["id"]
    completed_id = _upload_definition(client, no_tasks_bpmn, name="NoTasks").json()["id"]
    client.post(f"/process-definitions/{running_id}/instances", json={"created_by": "alice"})
    client.post(f"/process-definitions/{completed_id}/instances", json={"created_by": "alice"})

    running = client.get("/instances", params={"status": "running"}).json()
    completed = client.get("/instances", params={"status": "completed"}).json()
    assert len(running) == 1
    assert len(completed) == 1
