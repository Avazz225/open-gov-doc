"""Lanes as an opt-in authorization boundary (P93-S1, ADR 0249) with a
two-lane model against the real permission-service: a group-bound lane
("Sachbearbeitung") and a role-bound lane ("Freigabe"), then an unbound one.
Covers member, non-member, delegate and supervisor reassign, the
"Meine Aufgaben" filter, and the upload checks."""

import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from workflow_service.main import app

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")
FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "lane_bindings.bpmn")


@pytest.fixture
def client():
    with TestClient(app, headers={"X-DMS-Principal": "workflow-service-tests"}) as c:
        yield c


def _as(principal: str) -> dict[str, str]:
    return {"X-DMS-Principal": principal}


@pytest.fixture
def lanes(users_admin_headers):
    """A fresh group (members lena, lars) and role (held by frida) plus the
    principals' names, unique per test so runs don't interfere."""
    suffix = uuid.uuid4().hex[:6]
    names = {
        "group": f"Sachbearbeitung-{suffix}",
        "role": f"Freigabe-{suffix}",
        "lena": f"lena-{suffix}",
        "lars": f"lars-{suffix}",
        "frida": f"frida-{suffix}",
        "otto": f"otto-{suffix}",  # in no lane
        "dora": f"dora-{suffix}",  # lena's deputy, in no lane herself
        "sven": f"sven-{suffix}",  # lena's supervisor, in no lane himself
    }
    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=30) as http:
        group = http.post(
            "/groups", json={"name": names["group"]}, headers=users_admin_headers
        ).json()
        for member in ("lena", "lars"):
            http.post(
                f"/groups/{group['id']}/members",
                json={"principal_id": names[member]},
                headers=users_admin_headers,
            ).raise_for_status()
        role = http.post(
            "/roles",
            json={"name": names["role"], "permissions": ["lane.test"]},
            headers=users_admin_headers,
        ).json()["role"]
        http.post(
            "/role-assignments",
            json={
                "principal_type": "user",
                "principal_id": names["frida"],
                "role_id": role["id"],
                "resource_id": "root",
            },
        ).raise_for_status()
        http.post(
            "/supervisor-assignments",
            json={"principal_id": names["lena"], "supervisor_principal_id": names["sven"]},
            headers=users_admin_headers,
        ).raise_for_status()
    return names


def _bpmn(group: str, role: str) -> str:
    with open(FIXTURE) as f:
        return f.read().replace("__GROUP__", group).replace("__ROLE__", role)


def _upload(client, xml: str, admin_headers) -> httpx.Response:
    return client.post(
        "/process-definitions",
        data={"name": f"Lanes-{uuid.uuid4().hex[:6]}"},
        files={"bpmn_xml": ("lanes.bpmn", xml, "application/xml")},
        headers=admin_headers,
    )


def _start(client, lanes, admin_headers) -> tuple[str, dict]:
    definition = _upload(client, _bpmn(lanes["group"], lanes["role"]), admin_headers)
    assert definition.status_code == 201, definition.text
    assert definition.json()["lane_bindings"] == {
        "Sachbearbeitung": {"group": lanes["group"]},
        "Freigabe": {"role": lanes["role"]},
    }
    instance = client.post(
        f"/process-definitions/{definition.json()['id']}/instances",
        json={"created_by": "carla"},
        headers=_as("carla"),
    ).json()
    [task] = client.get(f"/instances/{instance['id']}/tasks").json()
    return instance["id"], task


def _complete(client, instance_id, task_id, principal, **extra):
    return client.post(
        f"/instances/{instance_id}/tasks/{task_id}/complete",
        json={"completed_by": principal, **extra},
        headers=_as(principal),
    )


def test_upload_rejects_unknown_or_double_bindings(client, admin_headers, lanes):
    unknown = _upload(client, _bpmn("gibt-es-nicht", lanes["role"]), admin_headers)
    assert unknown.status_code == 422
    assert "gibt-es-nicht" in unknown.json()["detail"]
    double = _bpmn(lanes["group"], lanes["role"]).replace(
        '<camunda:property name="dmsGroup"',
        f'<camunda:property name="dmsRole" value="{lanes["role"]}" />'
        '<camunda:property name="dmsGroup"',
    )
    response = _upload(client, double, admin_headers)
    assert response.status_code == 422
    assert "zugleich an Gruppe und Rolle" in response.json()["detail"]


def test_group_lane_member_and_non_member(client, admin_headers, lanes):
    instance_id, task = _start(client, lanes, admin_headers)
    assert task["name"] == "Prüfen" and task["lane"] == "Sachbearbeitung"

    denied = client.post(
        f"/instances/{instance_id}/tasks/{task['id']}/claim", json={}, headers=_as(lanes["otto"])
    )
    assert denied.status_code == 403
    assert "Sachbearbeitung" in denied.json()["detail"]
    assert _complete(client, instance_id, task["id"], lanes["otto"]).status_code == 403

    claimed = client.post(
        f"/instances/{instance_id}/tasks/{task['id']}/claim", json={}, headers=_as(lanes["lena"])
    )
    assert claimed.status_code == 200
    assert _complete(client, instance_id, task["id"], lanes["lena"]).status_code == 200

    # The next task is in the role-bound lane.
    [freigeben] = client.get(f"/instances/{instance_id}/tasks").json()
    assert freigeben["lane"] == "Freigabe"
    assert _complete(client, instance_id, freigeben["id"], lanes["lena"]).status_code == 403
    assert _complete(client, instance_id, freigeben["id"], lanes["frida"]).status_code == 200

    # The unbound lane behaves as before: anyone may work on it.
    [ablegen] = client.get(f"/instances/{instance_id}/tasks").json()
    assert ablegen["lane"] == "Registratur"
    assert _complete(client, instance_id, ablegen["id"], lanes["otto"]).status_code == 200


def test_reassign_needs_an_eligible_receiver_also_from_a_supervisor(client, admin_headers, lanes):
    instance_id, task = _start(client, lanes, admin_headers)
    base = f"/instances/{instance_id}/tasks/{task['id']}"
    assert client.post(f"{base}/claim", json={}, headers=_as(lanes["lena"])).status_code == 200

    to_outsider = client.post(
        f"{base}/reassign", json={"new_principal_id": lanes["otto"]}, headers=_as(lanes["lena"])
    )
    assert to_outsider.status_code == 403
    assert lanes["otto"] in to_outsider.json()["detail"]

    # sven supervises lena without being in the lane himself: he may move
    # her task, but only to someone in the lane.
    by_supervisor = client.post(
        f"{base}/reassign", json={"new_principal_id": lanes["lars"]}, headers=_as(lanes["sven"])
    )
    assert by_supervisor.status_code == 200
    assert by_supervisor.json()["principal_id"] == lanes["lars"]


def test_a_deputy_completes_for_a_member(client, admin_headers, lanes):
    instance_id, task = _start(client, lanes, admin_headers)
    now = datetime.now(UTC)
    httpx.post(
        f"{PERMISSION_SERVICE_URL}/delegations",
        json={
            "deputy_principal_id": lanes["dora"],
            "starts_at": (now - timedelta(hours=1)).isoformat(),
            "ends_at": (now + timedelta(days=1)).isoformat(),
        },
        headers=_as(lanes["lena"]),
        timeout=30,
    ).raise_for_status()

    # dora alone is not in the lane ...
    assert _complete(client, instance_id, task["id"], lanes["dora"]).status_code == 403
    # ... but for lena, who is, she may complete it.
    for_lena = _complete(
        client, instance_id, task["id"], lanes["dora"], on_behalf_of_principal_id=lanes["lena"]
    )
    assert for_lena.status_code == 200, for_lena.text


def test_my_tasks_filter_and_eligible_flag(client, admin_headers, lanes):
    instance_id, task = _start(client, lanes, admin_headers)

    def mine(principal: str) -> list[dict]:
        tasks = client.get("/tasks", params={"eligible": "me"}, headers=_as(principal)).json()
        return [t for t in tasks if t["instance_id"] == instance_id]

    def everything(principal: str) -> list[dict]:
        tasks = client.get("/tasks", headers=_as(principal)).json()
        return [t for t in tasks if t["instance_id"] == instance_id]

    assert [t["id"] for t in mine(lanes["lena"])] == [task["id"]]
    assert mine(lanes["otto"]) == []
    # Visibility isn't restricted: otto still sees it, marked not eligible.
    [seen] = everything(lanes["otto"])
    assert seen["eligible"] is False
    assert seen["lane_binding"] == {"group": lanes["group"]}

    # Claimed by lars: no longer lena's to pick up, still lars's.
    base = f"/instances/{instance_id}/tasks/{task['id']}"
    assert client.post(f"{base}/claim", json={}, headers=_as(lanes["lars"])).status_code == 200
    assert mine(lanes["lena"]) == []
    assert [t["eligible"] for t in mine(lanes["lars"])] == [True]

    assert client.get("/tasks", params={"eligible": "alle"}).status_code == 422
