"""Administrative instance operations (P93-S2, ADR 0249 Part A c): skip a
ready task, send the instance back to a completed task, migrate it to a newer
version of its process. Real permission-service for the capability check;
events captured from the bus."""

import os
import uuid

import pytest
from dms_eventbus_client import Event
from fastapi.testclient import TestClient
from workflow_service.main import app

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _read(name: str) -> str:
    with open(os.path.join(FIXTURES, name)) as f:
        return f.read()


OPS = _read("instance_ops.bpmn")
# v2 inserts "Gegenlesen" between "Freigeben" and "Ablegen".
OPS_WITH_GEGENLESEN = OPS.replace(
    '<bpmn:sequenceFlow id="Flow_3" sourceRef="Task_Freigeben" targetRef="Task_Ablegen" />',
    '<bpmn:sequenceFlow id="Flow_3" sourceRef="Task_Freigeben" targetRef="Task_Gegenlesen" />'
    '<bpmn:manualTask id="Task_Gegenlesen" name="Gegenlesen">'
    "<bpmn:incoming>Flow_3</bpmn:incoming><bpmn:outgoing>Flow_5</bpmn:outgoing>"
    "</bpmn:manualTask>"
    '<bpmn:sequenceFlow id="Flow_5" sourceRef="Task_Gegenlesen" targetRef="Task_Ablegen" />',
).replace(
    "<bpmn:incoming>Flow_3</bpmn:incoming>\n      <bpmn:outgoing>Flow_4</bpmn:outgoing>",
    "<bpmn:incoming>Flow_5</bpmn:incoming>\n      <bpmn:outgoing>Flow_4</bpmn:outgoing>",
)
# v2 without "Freigeben": "Prüfen" leads straight to "Ablegen".
OPS_WITHOUT_FREIGEBEN = OPS.replace(
    """    <bpmn:sequenceFlow id="Flow_2" sourceRef="Task_Pruefen" targetRef="Task_Freigeben" />
    <bpmn:manualTask id="Task_Freigeben" name="Freigeben">
      <bpmn:incoming>Flow_2</bpmn:incoming>
      <bpmn:outgoing>Flow_3</bpmn:outgoing>
    </bpmn:manualTask>
    <bpmn:sequenceFlow id="Flow_3" sourceRef="Task_Freigeben" targetRef="Task_Ablegen" />""",
    '    <bpmn:sequenceFlow id="Flow_3" sourceRef="Task_Pruefen" targetRef="Task_Ablegen" />',
).replace("<bpmn:outgoing>Flow_2</bpmn:outgoing>", "<bpmn:outgoing>Flow_3</bpmn:outgoing>")


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "workflow-service-tests"}) as c:
        yield c


@pytest.fixture
def events(monkeypatch):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)
    return published


def _as(principal: str) -> dict[str, str]:
    return {"X-DMS-Principal": principal}


def _upload(client, xml: str, name: str, admin_headers) -> dict:
    response = client.post(
        "/process-definitions",
        data={"name": name},
        files={"bpmn_xml": ("ops.bpmn", xml, "application/xml")},
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _start(client, definition_id: int) -> str:
    response = client.post(
        f"/process-definitions/{definition_id}/instances",
        json={"created_by": "carla"},
        headers=_as("carla"),
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _tasks(client, instance_id: str) -> list[dict]:
    return client.get(f"/instances/{instance_id}/tasks").json()


def _complete(client, instance_id: str, task_id: str, principal: str = "carla"):
    response = client.post(
        f"/instances/{instance_id}/tasks/{task_id}/complete",
        json={"completed_by": principal},
        headers=_as(principal),
    )
    assert response.status_code == 200, response.text


@pytest.fixture
def ops(client, admin_headers) -> tuple[str, int]:
    """A fresh family name and its first version."""
    name = f"Ops-{uuid.uuid4().hex[:6]}"
    return name, _upload(client, OPS, name, admin_headers)["id"]


def test_skip_needs_capability_and_reason(client, ops, instance_admin_headers):
    instance_id = _start(client, ops[1])
    [task] = _tasks(client, instance_id)
    url = f"/instances/{instance_id}/tasks/{task['id']}/skip"

    forbidden = client.post(url, json={"reason": "erledigt"}, headers=_as("carla"))
    assert forbidden.status_code == 403
    no_reason = client.post(url, json={"reason": ""}, headers=instance_admin_headers)
    assert no_reason.status_code == 422


def test_skip_moves_on_releases_claim_and_notifies(client, ops, instance_admin_headers, events):
    instance_id = _start(client, ops[1])
    [task] = _tasks(client, instance_id)
    claim = client.post(
        f"/instances/{instance_id}/tasks/{task['id']}/claim", json={}, headers=_as("bob")
    )
    assert claim.status_code == 200, claim.text

    response = client.post(
        f"/instances/{instance_id}/tasks/{task['id']}/skip",
        json={"reason": "Telefonisch geklärt"},
        headers=instance_admin_headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["target"] == "Prüfen"
    assert body["tasks_before"] == ["Prüfen"]
    assert body["tasks_after"] == ["Freigeben"]
    assert body["released_claims"] == ["bob"]
    assert [t["name"] for t in _tasks(client, instance_id)] == ["Freigeben"]
    [modified] = [e for e in events if e.event_type == "workflow.instance.modified"]
    assert modified.actor == instance_admin_headers["X-DMS-Principal"]
    assert modified.payload["operation"] == "skip"
    assert modified.payload["reason"] == "Telefonisch geklärt"
    assert modified.payload["affected_claimants"] == [
        {"principal_id": "bob", "task_id": task["id"], "task_name": "Prüfen"}
    ]


def test_skip_refuses_signature_task(client, admin_headers, instance_admin_headers):
    definition = _upload(
        client, _read("signature_task.bpmn"), f"Sig-{uuid.uuid4().hex[:6]}", admin_headers
    )
    instance_id = _start(client, definition["id"])
    [task] = _tasks(client, instance_id)

    response = client.post(
        f"/instances/{instance_id}/tasks/{task['id']}/skip",
        json={"reason": "eilt"},
        headers=instance_admin_headers,
    )

    assert response.status_code == 422
    assert "Signaturaufgabe" in response.json()["detail"]


def test_skip_refuses_parallel_section(client, admin_headers, instance_admin_headers):
    definition = _upload(
        client, _read("script_and_manual.bpmn"), f"Par-{uuid.uuid4().hex[:6]}", admin_headers
    )
    instance_id = _start(client, definition["id"])
    [task] = _tasks(client, instance_id)

    response = client.post(
        f"/instances/{instance_id}/tasks/{task['id']}/skip",
        json={"reason": "eilt"},
        headers=instance_admin_headers,
    )

    assert response.status_code == 422
    assert "parallel" in response.json()["detail"]


def test_skip_with_stale_version_conflicts(client, ops, instance_admin_headers):
    instance_id = _start(client, ops[1])
    version = client.get(f"/instances/{instance_id}").json()["workflow_version"]
    [task] = _tasks(client, instance_id)
    _complete(client, instance_id, task["id"])  # someone else moves on meanwhile
    [next_task] = _tasks(client, instance_id)

    response = client.post(
        f"/instances/{instance_id}/tasks/{next_task['id']}/skip",
        json={"reason": "eilt", "expected_version": version},
        headers=instance_admin_headers,
    )

    assert response.status_code == 409


def test_skip_refused_during_maintenance(client, ops, instance_admin_headers):
    instance_id = _start(client, ops[1])
    [task] = _tasks(client, instance_id)
    response = client.post(
        f"/instances/{instance_id}/tasks/{task['id']}/skip",
        json={"reason": "eilt"},
        headers={**instance_admin_headers, "X-DMS-Maintenance-Active": "true"},
    )
    assert response.status_code == 503


def test_send_back_to_completed_task(client, ops, instance_admin_headers, events):
    instance_id = _start(client, ops[1])
    [pruefen] = _tasks(client, instance_id)
    _complete(client, instance_id, pruefen["id"])
    [freigeben] = _tasks(client, instance_id)
    client.post(
        f"/instances/{instance_id}/tasks/{freigeben['id']}/claim", json={}, headers=_as("frida")
    )

    completed = client.get(
        f"/instances/{instance_id}/completed-tasks", headers=instance_admin_headers
    )
    assert completed.status_code == 200
    assert [t["name"] for t in completed.json()] == ["Prüfen"]

    response = client.post(
        f"/instances/{instance_id}/send-back",
        json={"target_task_id": completed.json()[0]["id"], "reason": "Unterlagen fehlen"},
        headers=instance_admin_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json()["tasks_after"] == ["Prüfen"]
    assert response.json()["released_claims"] == ["frida"]
    assert [t["name"] for t in _tasks(client, instance_id)] == ["Prüfen"]
    [modified] = [e for e in events if e.event_type == "workflow.instance.modified"]
    assert modified.payload["operation"] == "send_back"
    assert modified.payload["affected_claimants"][0]["principal_id"] == "frida"


def test_send_back_to_unknown_target_is_refused(client, ops, instance_admin_headers):
    instance_id = _start(client, ops[1])
    response = client.post(
        f"/instances/{instance_id}/send-back",
        json={"target_task_id": str(uuid.uuid4()), "reason": "Unterlagen fehlen"},
        headers=instance_admin_headers,
    )
    assert response.status_code == 422


def test_migration_preview_and_migrate(client, ops, admin_headers, instance_admin_headers):
    name, v1 = ops
    instance_id = _start(client, v1)
    [pruefen] = _tasks(client, instance_id)
    _complete(client, instance_id, pruefen["id"])
    v2 = _upload(client, OPS_WITH_GEGENLESEN, name, admin_headers)
    assert v2["version"] == 2

    preview = client.post(
        f"/instances/{instance_id}/migration-preview",
        json={"target_definition_id": v2["id"]},
        headers=instance_admin_headers,
    )
    assert preview.status_code == 200, preview.text
    report = preview.json()
    assert report["possible"] is True
    assert report["added"] == ["Gegenlesen"]
    assert report["active_tasks"] == [
        {"name": "Freigeben", "state": "READY", "aligned_to": "Freigeben"}
    ]

    migrated = client.post(
        f"/instances/{instance_id}/migrate",
        json={"target_definition_id": v2["id"], "reason": "Vier-Augen-Prinzip ergänzt"},
        headers=instance_admin_headers,
    )
    assert migrated.status_code == 200, migrated.text
    assert migrated.json()["instance"]["process_definition_id"] == v2["id"]

    [freigeben] = _tasks(client, instance_id)
    _complete(client, instance_id, freigeben["id"])
    assert [t["name"] for t in _tasks(client, instance_id)] == ["Gegenlesen"]


def test_migration_refused_when_active_task_disappears(
    client, ops, admin_headers, instance_admin_headers
):
    name, v1 = ops
    instance_id = _start(client, v1)
    [pruefen] = _tasks(client, instance_id)
    _complete(client, instance_id, pruefen["id"])  # now at "Freigeben"
    v2 = _upload(client, OPS_WITHOUT_FREIGEBEN, name, admin_headers)

    preview = client.post(
        f"/instances/{instance_id}/migration-preview",
        json={"target_definition_id": v2["id"]},
        headers=instance_admin_headers,
    ).json()
    assert preview["possible"] is False
    assert preview["removed"] == ["Freigeben"]
    assert "Freigeben" in preview["reasons"][0]

    migrated = client.post(
        f"/instances/{instance_id}/migrate",
        json={"target_definition_id": v2["id"], "reason": "trotzdem"},
        headers=instance_admin_headers,
    )
    assert migrated.status_code == 422
    assert client.get(f"/instances/{instance_id}").json()["process_definition_id"] == v1


def test_migration_target_must_be_newer_version_of_same_family(
    client, ops, admin_headers, instance_admin_headers
):
    _name, v1 = ops
    instance_id = _start(client, v1)
    other = _upload(client, OPS, f"Other-{uuid.uuid4().hex[:6]}", admin_headers)

    for target in (v1, other["id"]):
        response = client.post(
            f"/instances/{instance_id}/migration-preview",
            json={"target_definition_id": target},
            headers=instance_admin_headers,
        )
        assert response.status_code == 422
        assert "neuere Version" in response.json()["detail"]


# --- Terminate (P95-S1c) -----------------------------------------------------


def test_terminate_ends_the_instance_releases_claims_and_notifies(
    client, ops, instance_admin_headers, events
):
    instance_id = _start(client, ops[1])
    [task] = _tasks(client, instance_id)
    url = f"/instances/{instance_id}/terminate"
    assert client.post(url, json={"reason": "Testrest"}, headers=_as("carla")).status_code == 403
    assert client.post(url, json={"reason": ""}, headers=instance_admin_headers).status_code == 422
    claim = client.post(
        f"/instances/{instance_id}/tasks/{task['id']}/claim", json={}, headers=_as("bob")
    )
    assert claim.status_code == 200, claim.text

    response = client.post(url, json={"reason": "Testrest"}, headers=instance_admin_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["instance"]["status"] == "terminated"
    assert body["instance"]["completed_at"]
    assert body["tasks_before"] == ["Prüfen"] and body["tasks_after"] == []
    assert body["released_claims"] == ["bob"]
    assert _tasks(client, instance_id) == []
    assert all(t["instance_id"] != instance_id for t in client.get("/tasks").json())
    [modified] = [e for e in events if e.event_type == "workflow.instance.modified"]
    assert modified.payload["operation"] == "terminate"
    assert modified.payload["reason"] == "Testrest"
    assert modified.payload["affected_claimants"][0]["principal_id"] == "bob"
    # Not a completion: a case waiting for the process must not close.
    assert not [e for e in events if e.event_type == "workflow.instance.completed"]
    # A terminated instance is final.
    again = client.post(url, json={"reason": "nochmal"}, headers=instance_admin_headers)
    assert again.status_code == 409
    listed = client.get("/instances", params={"status": "terminated"}).json()
    assert instance_id in {i["id"] for i in listed}
