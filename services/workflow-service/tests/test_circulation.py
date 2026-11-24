"""Circulation template with an editable route (P93-S3, ADR 0249 Part A b):
the seeded "Umlauf" definition runs one user task per route entry; future
steps can be added, struck and reordered, the current one recalled and
redirected. Real permission-service for groups and supervisors; events are
captured from the bus."""

import os
import uuid

import httpx
import pytest
from dms_eventbus_client import Event
from fastapi.testclient import TestClient
from workflow_service.main import app

PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")


@pytest.fixture
def client():
    # The lifespan seeds the template (after the per-test truncate).
    with TestClient(app, headers={"X-DMS-Principal": "workflow-service-tests"}) as c:
        yield c


@pytest.fixture
def events(monkeypatch):
    published: list[Event] = []

    async def fake_publish(subject: str, data: bytes) -> None:
        published.append(Event.from_bytes(data))

    monkeypatch.setattr(app.state.event_bus, "publish", fake_publish)
    return published


@pytest.fixture
def people() -> dict[str, str]:
    suffix = uuid.uuid4().hex[:6]
    return {name: f"{name}-{suffix}" for name in ("alice", "bob", "carol", "dora", "otto", "sven")}


def _as(principal: str) -> dict[str, str]:
    return {"X-DMS-Principal": principal}


def _template_id(client) -> int:
    [template] = client.get("/process-definitions", params={"name": "Umlauf"}).json()
    return template["id"]


def _step(kind: str, assignee: str, assignee_type: str = "principal", note: str | None = None):
    return {"kind": kind, "assignee_type": assignee_type, "assignee": assignee, "note": note}


def _start(client, initiator: str, route: list[dict]) -> httpx.Response:
    return client.post(
        f"/process-definitions/{_template_id(client)}/instances",
        json={"business_key": None, "initial_data": {"dms_route": route}},
        headers=_as(initiator),
    )


def _started(client, initiator: str, route: list[dict]) -> str:
    response = _start(client, initiator, route)
    assert response.status_code == 201, response.text
    return response.json()["id"]


def _task(client, instance_id: str) -> dict:
    [task] = client.get(f"/instances/{instance_id}/tasks").json()
    return task


def _complete(client, instance_id: str, principal: str) -> httpx.Response:
    return client.post(
        f"/instances/{instance_id}/tasks/{_task(client, instance_id)['id']}/complete",
        json={},
        headers=_as(principal),
    )


def _route(client, instance_id: str, principal: str) -> dict:
    response = client.get(f"/instances/{instance_id}/route", headers=_as(principal))
    assert response.status_code == 200, response.text
    return response.json()


def _open_assignees(route: dict) -> list[tuple[str, str]]:
    return [
        (s["assignee"], s["status"])
        for s in route["steps"]
        if s["status"] in ("done", "current", "pending")
    ]


def test_template_is_seeded_as_circulation(client):
    [template] = client.get("/process-definitions", params={"name": "Umlauf"}).json()
    assert template["is_circulation"] is True
    assert template["version"] == 1


def test_start_needs_a_valid_route(client, people):
    assert _start(client, people["alice"], []).status_code == 422
    bad_kind = _start(client, people["alice"], [_step("abnicken", people["bob"])])
    assert bad_kind.status_code == 422
    unknown_group = _start(client, people["alice"], [_step("kenntnis", "gibt-es-nicht", "group")])
    assert unknown_group.status_code == 422
    assert "gibt-es-nicht" in unknown_group.json()["detail"]


def test_route_runs_step_by_step_and_only_the_assignee_may_work(client, people):
    instance_id = _started(
        client,
        people["alice"],
        [
            _step("mitzeichnen", people["bob"], note="Bitte prüfen"),
            _step("zeichnen", people["carol"]),
        ],
    )

    task = _task(client, instance_id)
    assert task["circulation_step"]["assignee"] == people["bob"]
    assert task["circulation_step"]["kind"] == "mitzeichnen"
    assert (task["circulation_step"]["position"], task["circulation_step"]["total"]) == (1, 2)
    refused = client.post(
        f"/instances/{instance_id}/tasks/{task['id']}/claim", json={}, headers=_as(people["carol"])
    )
    assert refused.status_code == 403
    assert "nicht zuständig" in refused.json()["detail"]
    mine = client.get("/tasks", params={"eligible": "me"}, headers=_as(people["bob"])).json()
    assert [t["instance_id"] for t in mine] == [instance_id]
    assert (
        client.get("/tasks", params={"eligible": "me"}, headers=_as(people["carol"])).json() == []
    )

    assert _complete(client, instance_id, people["bob"]).status_code == 200
    assert _task(client, instance_id)["circulation_step"]["assignee"] == people["carol"]
    assert _complete(client, instance_id, people["carol"]).status_code == 200

    assert client.get(f"/instances/{instance_id}").json()["status"] == "completed"
    route = _route(client, instance_id, people["alice"])
    assert [(s["assignee"], s["status"], s["closed_by"]) for s in route["steps"]] == [
        (people["bob"], "done", people["bob"]),
        (people["carol"], "done", people["carol"]),
    ]
    assert route["can_edit"] is False  # finished


def test_add_a_co_signer_mid_circulation(client, people, events):
    instance_id = _started(
        client,
        people["alice"],
        [_step("mitzeichnen", people["bob"]), _step("zeichnen", people["carol"])],
    )
    version = client.get(f"/instances/{instance_id}").json()["workflow_version"]

    response = client.post(
        f"/instances/{instance_id}/route/steps",
        json={
            "step": _step("mitzeichnen", people["dora"], note="Haushalt"),
            "index": 0,
            "reason": "Haushaltsmittel betroffen",
            "expected_version": version,
        },
        headers=_as(people["alice"]),
    )

    assert response.status_code == 200, response.text
    assert _open_assignees(response.json()) == [
        (people["bob"], "current"),
        (people["dora"], "pending"),
        (people["carol"], "pending"),
    ]
    [modified] = [e for e in events if e.event_type == "workflow.instance.modified"]
    assert modified.payload["operation"] == "route_add"
    assert modified.payload["notify_principals"] == [people["dora"]]
    assert modified.payload["reason"] == "Haushaltsmittel betroffen"

    # The new step is really walked through: bob -> dora -> carol.
    _complete(client, instance_id, people["bob"])
    assert _task(client, instance_id)["circulation_step"]["assignee"] == people["dora"]
    assert _task(client, instance_id)["circulation_step"]["total"] == 3


def test_stale_version_conflicts(client, people):
    instance_id = _started(client, people["alice"], [_step("mitzeichnen", people["bob"])])
    version = client.get(f"/instances/{instance_id}").json()["workflow_version"]
    _complete_ok = client.post(
        f"/instances/{instance_id}/route/steps",
        json={"step": _step("kenntnis", people["dora"]), "reason": "zur Kenntnis"},
        headers=_as(people["alice"]),
    )
    assert _complete_ok.status_code == 200

    stale = client.post(
        f"/instances/{instance_id}/route/steps",
        json={
            "step": _step("kenntnis", people["carol"]),
            "reason": "zur Kenntnis",
            "expected_version": version,
        },
        headers=_as(people["alice"]),
    )
    assert stale.status_code == 409


def test_strike_and_reorder_future_steps_only(client, people):
    instance_id = _started(
        client,
        people["alice"],
        [
            _step("mitzeichnen", people["bob"]),
            _step("mitzeichnen", people["carol"]),
            _step("zeichnen", people["dora"]),
        ],
    )
    steps = _route(client, instance_id, people["alice"])["steps"]
    current, carol, dora = steps

    struck_current = client.post(
        f"/instances/{instance_id}/route/steps/{current['id']}/remove",
        json={"reason": "nicht mehr zuständig"},
        headers=_as(people["alice"]),
    )
    assert struck_current.status_code == 422
    assert "zurückgeholt" in struck_current.json()["detail"]

    reordered = client.put(
        f"/instances/{instance_id}/route/order",
        json={"step_ids": [dora["id"], carol["id"]], "reason": "Zeichnung vorziehen"},
        headers=_as(people["alice"]),
    )
    assert reordered.status_code == 200, reordered.text
    assert [a for a, _ in _open_assignees(reordered.json())] == [
        people["bob"],
        people["dora"],
        people["carol"],
    ]

    struck = client.post(
        f"/instances/{instance_id}/route/steps/{carol['id']}/remove",
        json={"reason": "im Urlaub"},
        headers=_as(people["alice"]),
    )
    assert struck.status_code == 200
    removed = next(s for s in struck.json()["steps"] if s["id"] == carol["id"])
    assert (removed["status"], removed["close_reason"]) == ("removed", "im Urlaub")

    _complete(client, instance_id, people["bob"])
    _complete(client, instance_id, people["dora"])
    assert client.get(f"/instances/{instance_id}").json()["status"] == "completed"

    finished = client.post(
        f"/instances/{instance_id}/route/steps",
        json={"step": _step("kenntnis", people["otto"]), "reason": "zu spät"},
        headers=_as(people["alice"]),
    )
    assert finished.status_code == 409


def test_recall_and_send_back_to_the_author(client, people, events):
    instance_id = _started(
        client,
        people["alice"],
        [_step("mitzeichnen", people["bob"]), _step("zeichnen", people["carol"])],
    )
    task = _task(client, instance_id)
    claim = client.post(
        f"/instances/{instance_id}/tasks/{task['id']}/claim", json={}, headers=_as(people["bob"])
    )
    assert claim.status_code == 200, claim.text

    response = client.post(
        f"/instances/{instance_id}/route/recall",
        json={
            "redirect": _step("bearbeiten", people["alice"], note="Bitte Anlage ergänzen"),
            "reason": "Anlage fehlt",
        },
        headers=_as(people["alice"]),
    )

    assert response.status_code == 200, response.text
    statuses = [(s["assignee"], s["status"]) for s in response.json()["steps"]]
    assert statuses == [
        (people["bob"], "recalled"),
        (people["alice"], "current"),
        (people["carol"], "pending"),
    ]
    assert _task(client, instance_id)["claimed_by"] is None
    [modified] = [e for e in events if e.event_type == "workflow.instance.modified"]
    assert modified.payload["operation"] == "route_recall"
    assert modified.payload["affected_claimants"][0]["principal_id"] == people["bob"]
    assert modified.payload["notify_principals"] == []  # alice acted herself

    # The author reworks, then the folder continues with carol.
    assert _complete(client, instance_id, people["bob"]).status_code == 403
    assert _complete(client, instance_id, people["alice"]).status_code == 200
    assert _task(client, instance_id)["circulation_step"]["assignee"] == people["carol"]


def test_who_may_edit_the_route(client, people, users_admin_headers):
    instance_id = _started(client, people["alice"], [_step("mitzeichnen", people["bob"])])
    body = {"step": _step("kenntnis", people["dora"]), "reason": "zur Kenntnis"}

    assert _route(client, instance_id, people["otto"])["can_edit"] is False
    outsider = client.post(
        f"/instances/{instance_id}/route/steps", json=body, headers=_as(people["otto"])
    )
    assert outsider.status_code == 403

    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=30) as http:
        http.post(
            "/supervisor-assignments",
            json={"principal_id": people["alice"], "supervisor_principal_id": people["sven"]},
            headers=users_admin_headers,
        ).raise_for_status()
    assert _route(client, instance_id, people["sven"])["can_edit"] is True
    supervisor = client.post(
        f"/instances/{instance_id}/route/steps", json=body, headers=_as(people["sven"])
    )
    assert supervisor.status_code == 200, supervisor.text


def test_group_step_is_open_to_its_members(client, people, users_admin_headers):
    group = f"Kasse-{uuid.uuid4().hex[:6]}"
    with httpx.Client(base_url=PERMISSION_SERVICE_URL, timeout=30) as http:
        created = http.post("/groups", json={"name": group}, headers=users_admin_headers).json()
        http.post(
            f"/groups/{created['id']}/members",
            json={"principal_id": people["dora"]},
            headers=users_admin_headers,
        ).raise_for_status()
    instance_id = _started(client, people["alice"], [_step("kenntnis", group, "group")])

    assert _complete(client, instance_id, people["otto"]).status_code == 403
    assert _complete(client, instance_id, people["dora"]).status_code == 200
    assert client.get(f"/instances/{instance_id}").json()["status"] == "completed"


def test_admin_skip_of_a_circulation_step_is_refused(client, people, instance_admin_headers):
    instance_id = _started(client, people["alice"], [_step("mitzeichnen", people["bob"])])
    response = client.post(
        f"/instances/{instance_id}/tasks/{_task(client, instance_id)['id']}/skip",
        json={"reason": "eilt"},
        headers=instance_admin_headers,
    )
    assert response.status_code == 422
    assert "Laufweg" in response.json()["detail"]
