import uuid

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.testclient import TestClient
from fleet_management_service.main import app

FLEET_KEY = "fleet-secret-xyz"


def _make_stub(*, license_installed: bool = True) -> FastAPI:
    """Simuliert den Gateway einer verwalteten Installation - dieselben drei
    Pfade, über die `FleetAgentClient` tatsächlich spricht (`agent_client.py`).
    Prüft den Fleet-Agent-Schlüssel exakt wie `license-service`/`config-service`
    es in echt tun (`_is_fleet_agent`), damit ein Test mit falschem Schlüssel
    denselben Fehlerpfad auslöst."""
    stub = FastAPI()

    @stub.get("/api/registry-service/installation")
    def _installation() -> dict:
        return {"id": "kunde-nord-001", "display_name": "Kunde Nord GmbH"}

    @stub.get("/api/license-service/license/status")
    def _license_status() -> dict:
        return {"installed": license_installed, "valid": license_installed}

    @stub.post("/api/license-service/license")
    async def _upload_license(request: Request, authorization: str = Header(default="")) -> dict:
        if authorization != f"Bearer {FLEET_KEY}":
            raise HTTPException(status_code=403, detail="Fehlender/ungueltiger Fleet-Agent-Key")
        body = await request.json()
        return {"installed": True, "valid": True, "license_token": body["license_token"]}

    @stub.post("/api/config-service/config/import")
    async def _import_config(request: Request, authorization: str = Header(default="")) -> dict:
        if authorization != f"Bearer {FLEET_KEY}":
            raise HTTPException(status_code=403, detail="Fehlender/ungueltiger Fleet-Agent-Key")
        body = await request.json()
        return {"schema_version": body.get("schema_version", "1.0"), "results": []}

    return stub


@pytest.fixture
def client():
    with TestClient(app) as c:
        app.state.agent_transport = httpx.ASGITransport(app=_make_stub())
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "fleet-management-service"


def _register(client, **overrides) -> dict:
    payload = {
        "display_name": "Kunde Nord GmbH",
        "gateway_base_url": "http://fake-gateway.test",
        "fleet_agent_api_key": FLEET_KEY,
    }
    payload.update(overrides)
    response = client.post("/installations", json=payload)
    assert response.status_code == 201
    return response.json()


def test_create_installation_returns_key_once(client):
    body = _register(client)
    assert body["fleet_agent_api_key"] == FLEET_KEY
    assert body["display_name"] == "Kunde Nord GmbH"


def test_create_installation_without_key_generates_one(client):
    body = _register(client, fleet_agent_api_key=None)
    assert body["fleet_agent_api_key"]


def test_list_installations_omits_key(client):
    created = _register(client)
    response = client.get("/installations")
    assert response.status_code == 200
    entries = {entry["id"]: entry for entry in response.json()}
    assert created["id"] in entries
    assert "fleet_agent_api_key" not in entries[created["id"]]


def test_delete_installation(client):
    created = _register(client)
    response = client.delete(f"/installations/{created['id']}")
    assert response.status_code == 204
    remaining_ids = {entry["id"] for entry in client.get("/installations").json()}
    assert created["id"] not in remaining_ids


def test_delete_unknown_installation_returns_404(client):
    response = client.delete("/installations/does-not-exist")
    assert response.status_code == 404


def test_get_installation_status_reachable(client):
    created = _register(client)
    response = client.get(f"/installations/{created['id']}/status")
    assert response.status_code == 200
    body = response.json()
    assert body["reachable"] is True
    assert body["installation_id"] == "kunde-nord-001"
    assert body["installation_display_name"] == "Kunde Nord GmbH"
    assert body["license_status"]["installed"] is True


def test_get_installation_status_unreachable_reports_error_not_exception(client):
    created = _register(client)
    app.state.agent_transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("no route", request=request))
    )
    response = client.get(f"/installations/{created['id']}/status")
    assert response.status_code == 200
    body = response.json()
    assert body["reachable"] is False
    assert body["error"]


def test_list_installation_statuses_aggregates_all(client):
    first = _register(client, display_name="A")
    second = _register(client, display_name="B", gateway_base_url="http://fake-gateway-2.test")
    response = client.get("/installations/status")
    assert response.status_code == 200
    statuses = {entry["id"]: entry for entry in response.json()}
    assert statuses[first["id"]]["reachable"] is True
    assert statuses[second["id"]]["reachable"] is True


def test_push_license_forwards_token_to_agent(client):
    created = _register(client)
    response = client.post(
        f"/installations/{created['id']}/license", json={"license_token": "a.b.c"}
    )
    assert response.status_code == 200
    assert response.json()["license_token"] == "a.b.c"


def test_push_license_with_wrong_stored_key_returns_502(client):
    created = _register(client, fleet_agent_api_key="wrong-key")
    response = client.post(
        f"/installations/{created['id']}/license", json={"license_token": "a.b.c"}
    )
    assert response.status_code == 502


def test_provision_forwards_config_document_to_agent(client):
    created = _register(client)
    response = client.post(
        f"/installations/{created['id']}/provision",
        json={"config_document": {"schema_version": "1.0"}, "categories": ["roles"]},
    )
    assert response.status_code == 200
    assert response.json()["schema_version"] == "1.0"


def test_license_and_provision_on_unknown_installation_return_404(client):
    response = client.post("/installations/does-not-exist/license", json={"license_token": "x"})
    assert response.status_code == 404
    response = client.post("/installations/does-not-exist/provision", json={"config_document": {}})
    assert response.status_code == 404


# --- Flotten-Update-Orchestrierung (3a-Erweiterung, P13-S2b) ----------------


def _simple_plan_steps() -> list[dict]:
    return [
        {"name": "Bereichssperre setzen (4.7)", "step_type": "gate", "requires_approval": False},
        {"name": "Verifikation", "step_type": "verify", "requires_approval": False},
        {"name": "Freigabe", "step_type": "gate", "requires_approval": True},
    ]


def _create_plan(client, **overrides) -> dict:
    payload = {"name": "Standard-Update", "version": "1.0", "steps": _simple_plan_steps()}
    payload.update(overrides)
    response = client.post("/plans", json=payload)
    assert response.status_code == 201
    return response.json()


def test_create_plan_rejects_unknown_step_type(client):
    response = client.post(
        "/plans",
        json={
            "name": "Kaputter Plan",
            "version": "1.0",
            "steps": [{"name": "X", "step_type": "does-not-exist"}],
        },
    )
    assert response.status_code == 422


def test_create_plan_rejects_empty_steps(client):
    response = client.post("/plans", json={"name": "Leer", "version": "1.0", "steps": []})
    assert response.status_code == 422


def test_group_create_and_membership(client):
    group = client.post("/groups", json={"name": f"Welle-{uuid.uuid4().hex[:8]}"}).json()
    assert group["installation_ids"] == []
    installation = _register(client)

    add_response = client.post(
        f"/groups/{group['id']}/members", json={"installation_id": installation["id"]}
    )
    assert add_response.status_code == 200
    assert installation["id"] in add_response.json()["installation_ids"]

    remove_response = client.delete(f"/groups/{group['id']}/members/{installation['id']}")
    assert installation["id"] not in remove_response.json()["installation_ids"]


def test_create_rollout_from_group_resolves_members(client):
    group = client.post("/groups", json={"name": f"Welle-{uuid.uuid4().hex[:8]}"}).json()
    installation = _register(client)
    client.post(f"/groups/{group['id']}/members", json={"installation_id": installation["id"]})
    plan = _create_plan(client)

    response = client.post(
        "/rollouts",
        json={"plan_id": plan["id"], "name": "Testwelle", "group_id": group["id"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    run_installation_ids = {r["installation_id"] for r in body["runs"]}
    assert installation["id"] in run_installation_ids
    assert body["runs"][0]["current_step_name"] == "Bereichssperre setzen (4.7)"


def test_create_rollout_with_empty_target_set_returns_422(client):
    plan = _create_plan(client)
    response = client.post("/rollouts", json={"plan_id": plan["id"], "name": "Leer"})
    assert response.status_code == 422


def _start_rollout_for_one_installation(client) -> tuple[dict, dict]:
    installation = _register(client)
    plan = _create_plan(client)
    rollout = client.post(
        "/rollouts",
        json={"plan_id": plan["id"], "name": "Testwelle", "include": [installation["id"]]},
    ).json()
    started = client.post(f"/rollouts/{rollout['id']}/start", json={"started_by": "alice"}).json()
    return started, installation


def test_start_rollout_requires_draft_status(client):
    started, _installation = _start_rollout_for_one_installation(client)
    response = client.post(f"/rollouts/{started['id']}/start", json={"started_by": "alice"})
    assert response.status_code == 409


def test_full_rollout_happy_path_through_all_step_types(client):
    started, installation = _start_rollout_for_one_installation(client)
    rollout_id = started["id"]
    installation_id = installation["id"]
    run = started["runs"][0]
    assert run["status"] == "wait_external"
    assert run["current_step_index"] == 0

    # Schritt 0: "gate", keine Freigabe noetig - direkt bestaetigt.
    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={"actor": "operator-a"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["current_step_index"] == 1
    assert body["status"] == "wait_external"

    # Schritt 1: "verify" - automatischer Check ueber den Stub (reachable+valid).
    response = client.post(f"/rollouts/{rollout_id}/installations/{installation_id}/advance")
    assert response.status_code == 200
    body = response.json()
    assert body["current_step_index"] == 2
    assert body["status"] == "wait_external"

    # Schritt 2: "gate" mit requires_approval - mark-done schlaegt nur vor.
    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={"actor": "operator-a"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "manual_required"
    assert body["proposed_by"] == "operator-a"

    # Dieselbe Person darf nicht freigeben (4.3).
    same_actor = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/approve",
        json={"actor": "operator-a"},
    )
    assert same_actor.status_code == 409

    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/approve",
        json={"actor": "operator-b"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["current_step_name"] is None


def test_mark_done_rejects_verify_step(client):
    started, installation = _start_rollout_for_one_installation(client)
    rollout_id, installation_id = started["id"], installation["id"]
    client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={"actor": "a"},
    )
    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={"actor": "a"},
    )
    assert response.status_code == 400


def test_advance_rejects_gate_step(client):
    started, installation = _start_rollout_for_one_installation(client)
    response = client.post(f"/rollouts/{started['id']}/installations/{installation['id']}/advance")
    assert response.status_code == 400


def test_mark_done_recoverable_failed_then_retry(client):
    started, installation = _start_rollout_for_one_installation(client)
    rollout_id, installation_id = started["id"], installation["id"]

    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={
            "actor": "a",
            "outcome": "recoverable_failed",
            "detail": "Rolling Update fehlgeschlagen",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "recoverable_failed"
    assert body["current_step_index"] == 0

    retry_response = client.post(f"/rollouts/{rollout_id}/installations/{installation_id}/retry")
    assert retry_response.status_code == 200
    assert retry_response.json()["status"] == "wait_external"

    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={"actor": "a"},
    )
    assert response.status_code == 200
    assert response.json()["current_step_index"] == 1


def test_mark_done_fatal_contract_requires_acknowledge(client):
    started, installation = _start_rollout_for_one_installation(client)
    rollout_id, installation_id = started["id"], installation["id"]

    client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={"actor": "a", "outcome": "fatal_contract"},
    )
    retry_response = client.post(f"/rollouts/{rollout_id}/installations/{installation_id}/retry")
    assert retry_response.status_code == 409

    ack_response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/acknowledge-fatal"
    )
    assert ack_response.status_code == 200
    assert ack_response.json()["status"] == "wait_external"


def test_verify_step_unreachable_installation_returns_retry_later(client):
    started, installation = _start_rollout_for_one_installation(client)
    rollout_id, installation_id = started["id"], installation["id"]
    client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={"actor": "a"},
    )

    app.state.agent_transport = httpx.MockTransport(
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("no route", request=request))
    )
    response = client.post(f"/rollouts/{rollout_id}/installations/{installation_id}/advance")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "retry_later"
    assert body["current_step_index"] == 1


def test_reject_approval_sets_recoverable_failed(client):
    started, installation = _start_rollout_for_one_installation(client)
    rollout_id, installation_id = started["id"], installation["id"]
    client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done", json={"actor": "a"}
    )
    client.post(f"/rollouts/{rollout_id}/installations/{installation_id}/advance")
    client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done", json={"actor": "a"}
    )

    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/reject",
        json={"actor": "b", "reason": "Verdacht auf Fehlkonfiguration"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "recoverable_failed"
    assert "Verdacht auf Fehlkonfiguration" in body["error_message"]
