import uuid

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.testclient import TestClient
from fleet_management_service.main import app, settings

FLEET_KEY = "fleet-secret-xyz"
# P54-S1/ADR 0172: distinct from FLEET_KEY (the per-installation agent key
# this service ITSELF mints) - this is the operator secret a caller now
# needs to reach this service's own API at all.
OPERATOR_KEY = "fleet-operator-secret-for-tests"


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

    @stub.get("/api/registry-service/replicas/summary")
    def _replica_summary(authorization: str = Header(default="")) -> dict:
        # P87-S3: gated like the license upload, by the fleet-agent key.
        if authorization != f"Bearer {FLEET_KEY}":
            raise HTTPException(status_code=403, detail="Fehlender/ungueltiger Fleet-Agent-Key")
        return {
            "counts": {"ok": 27, "under": 1, "vanished": 0, "over": 0, "unmonitored": 2},
            "alarms": [{"service_type": "ocr-service", "state": "under", "reachable": 1}],
        }

    @stub.post("/api/license-service/license")
    async def _upload_license(request: Request, authorization: str = Header(default="")) -> dict:
        if authorization != f"Bearer {FLEET_KEY}":
            raise HTTPException(status_code=403, detail="Fehlender/ungueltiger Fleet-Agent-Key")
        body = await request.json()
        return {"installed": True, "valid": True, "license_token": body["license_token"]}

    @stub.post("/api/config-service/config/fleet-import")
    async def _import_config(request: Request, authorization: str = Header(default="")) -> dict:
        if authorization != f"Bearer {FLEET_KEY}":
            raise HTTPException(status_code=403, detail="Fehlender/ungueltiger Fleet-Agent-Key")
        body = await request.json()
        return {"schema_version": body.get("schema_version", "1.0"), "results": []}

    return stub


@pytest.fixture
def client():
    """P54-S1/ADR 0172: `settings.fleet_operator_key` is set here and the
    resulting bearer header applied as a CLIENT-level default (not per test
    call site) - every one of this service's ~30 existing tests needed the
    header once the whole API was gated, unlike `federation-hub-service`'s
    own per-test set/reset pattern (ADR 0162), which only ever gated two of
    many endpoints. The two dedicated gate tests below override the header
    per-call instead of using this fixture's default."""
    settings.fleet_operator_key = OPERATOR_KEY
    try:
        with TestClient(app, headers={"Authorization": f"Bearer {OPERATOR_KEY}"}) as c:
            app.state.agent_transport = httpx.ASGITransport(app=_make_stub())
            yield c
    finally:
        settings.fleet_operator_key = None


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _mint_operator(client, name: str, *, is_admin: bool = False) -> str:
    """Mints an operator via the bootstrap (env key) admin and returns its token."""
    response = client.post("/operators", json={"name": name, "is_admin": is_admin})
    assert response.status_code == 201, response.text
    return response.json()["token"]


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "fleet-management-service"


def test_installations_requires_fleet_operator_key(client):
    """P54-S1/ADR 0172."""
    response = client.get("/installations", headers={"Authorization": ""})
    assert response.status_code == 403


def test_installations_with_wrong_operator_key_returns_403(client):
    """P54-S1/ADR 0172."""
    response = client.get("/installations", headers={"Authorization": "Bearer wrong-key"})
    assert response.status_code == 403


def test_healthz_stays_ungated_without_operator_key(client):
    """P54-S1/ADR 0172: `/healthz` is the one deliberate exception."""
    response = client.get("/healthz", headers={"Authorization": ""})
    assert response.status_code == 200


def test_cors_preflight_allows_admin_ui_origin(client):
    """P67-S1: admin-ui now calls this service directly from the browser
    (same reasoning as `federation-hub-service`'s own CORS setup) - this was
    never needed before this session, since no admin-ui ever called this
    service at all. Asserts the preflight OPTIONS response actually carries
    the allow-origin header, not just that CORSMiddleware was imported."""
    response = client.options(
        "/installations",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3001"


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


def test_rotate_key_generates_a_new_value_by_default(client):
    """Post-Roadmap Phase 21 Session 1 (ADR 0084)."""
    created = _register(client)
    response = client.post(f"/installations/{created['id']}/rotate-key", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["fleet_agent_api_key"]
    assert body["fleet_agent_api_key"] != FLEET_KEY


def test_rotate_key_accepts_an_operator_supplied_value(client):
    created = _register(client)
    response = client.post(
        f"/installations/{created['id']}/rotate-key",
        json={"fleet_agent_api_key": "already-set-on-the-installation"},
    )
    assert response.status_code == 200
    assert response.json()["fleet_agent_api_key"] == "already-set-on-the-installation"


def test_rotate_key_updates_the_key_used_for_outgoing_calls(client):
    """Bestätigt, dass die Rotation tatsächlich den Wert ersetzt, den
    `_agent_client` für ausgehende Aufrufe präsentiert - nicht nur die
    API-Antwort."""
    created = _register(client)
    rotate_response = client.post(
        f"/installations/{created['id']}/rotate-key",
        json={"fleet_agent_api_key": FLEET_KEY},
    )
    assert rotate_response.status_code == 200

    status_response = client.get(f"/installations/{created['id']}/status")
    assert status_response.status_code == 200
    assert status_response.json()["reachable"] is True


def test_rotate_key_unknown_installation_returns_404(client):
    response = client.post("/installations/does-not-exist/rotate-key", json={})
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
    # P87-S3 (ADR 0241 Decision 5): the installation's replica summary.
    assert body["replica_summary"]["counts"]["under"] == 1
    assert body["replica_summary"]["alarms"][0]["service_type"] == "ocr-service"


def test_replica_summary_missing_does_not_make_the_installation_unreachable(client):
    """An installation whose registry refuses the summary (an older version,
    or a different fleet-agent key there) stays reachable; only the summary
    is absent (P87-S3)."""
    created = _register(client, fleet_agent_api_key="some-other-key")
    response = client.get(f"/installations/{created['id']}/status")
    body = response.json()
    assert body["reachable"] is True
    assert body["replica_summary"] is None


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


def _start_rollout_for_one_installation(client, headers: dict | None = None) -> tuple[dict, dict]:
    installation = _register(client)
    plan = _create_plan(client)
    rollout = client.post(
        "/rollouts",
        json={"plan_id": plan["id"], "name": "Testwelle", "include": [installation["id"]]},
    ).json()
    started = client.post(f"/rollouts/{rollout['id']}/start", json={}, headers=headers).json()
    return started, installation


def test_start_rollout_requires_draft_status(client):
    started, _installation = _start_rollout_for_one_installation(client)
    response = client.post(f"/rollouts/{started['id']}/start", json={})
    assert response.status_code == 409


def test_full_rollout_happy_path_through_all_step_types(client):
    started, installation = _start_rollout_for_one_installation(client)
    rollout_id = started["id"]
    installation_id = installation["id"]
    run = started["runs"][0]
    assert run["status"] == "wait_external"
    assert run["current_step_index"] == 0

    # Two real operators (P84-S4, ADR 0234): the four-eyes check now compares
    # verified identities from their own tokens, not free-text actors.
    operator_a = _mint_operator(client, "operator-a")
    operator_b = _mint_operator(client, "operator-b")

    # Schritt 0: "gate", keine Freigabe noetig - direkt bestaetigt.
    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={},
        headers=_bearer(operator_a),
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
    # A spoofed `actor` in the body is ignored: the proposer is operator A.
    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={},
        headers=_bearer(operator_a),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "manual_required"
    assert body["proposed_by"] == "operator-a"

    # Dieselbe Person darf nicht freigeben (4.3).
    # Claiming to be someone else in the body doesn't help any more.
    same_actor = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/approve",
        json={},
        headers=_bearer(operator_a),
    )
    assert same_actor.status_code == 409

    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/approve",
        json={},
        headers=_bearer(operator_b),
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
        json={},
    )
    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={},
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
        json={},
    )
    assert response.status_code == 200
    assert response.json()["current_step_index"] == 1


def test_mark_done_fatal_contract_requires_acknowledge(client):
    started, installation = _start_rollout_for_one_installation(client)
    rollout_id, installation_id = started["id"], installation["id"]

    client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done",
        json={"outcome": "fatal_contract"},
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
        json={},
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
    client.post(f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done", json={})
    client.post(f"/rollouts/{rollout_id}/installations/{installation_id}/advance")
    client.post(f"/rollouts/{rollout_id}/installations/{installation_id}/mark-done", json={})

    response = client.post(
        f"/rollouts/{rollout_id}/installations/{installation_id}/reject",
        json={"reason": "Verdacht auf Fehlkonfiguration"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "recoverable_failed"
    assert "Verdacht auf Fehlkonfiguration" in body["error_message"]


# --- Operators (P84-S4, ADR 0234) -------------------------------------------


def test_bootstrap_operator_is_the_env_key_admin(client):
    me = client.get("/operators/me")
    assert me.status_code == 200
    assert me.json()["name"] == "bootstrap"
    assert me.json()["is_admin"] is True


def test_minted_operator_token_works_and_is_never_listed(client):
    token = _mint_operator(client, "olga")
    me = client.get("/operators/me", headers=_bearer(token))
    assert me.status_code == 200
    assert me.json() == {**me.json(), "name": "olga", "is_admin": False}
    assert client.get("/installations", headers=_bearer(token)).status_code == 200

    listed = client.get("/operators").json()
    assert {o["name"] for o in listed} >= {"bootstrap", "olga"}
    assert all("token" not in o and "token_hash" not in o for o in listed)


def test_only_admins_mint_or_revoke_operators(client):
    token = _mint_operator(client, "plain")
    target = client.post("/operators", json={"name": "target"}).json()

    assert client.post("/operators", json={"name": "x"}, headers=_bearer(token)).status_code == 403
    assert client.delete(f"/operators/{target['id']}", headers=_bearer(token)).status_code == 403

    admin = _mint_operator(client, "second-admin", is_admin=True)
    assert client.post("/operators", json={"name": "y"}, headers=_bearer(admin)).status_code == 201


def test_revoked_operator_token_stops_working(client):
    created = client.post("/operators", json={"name": "leaver"}).json()
    assert client.get("/operators/me", headers=_bearer(created["token"])).status_code == 200

    assert client.delete(f"/operators/{created['id']}").status_code == 204
    assert client.get("/operators/me", headers=_bearer(created["token"])).status_code == 403


def test_operator_names_are_unique_and_bootstrap_is_reserved(client):
    _mint_operator(client, "dup")
    assert client.post("/operators", json={"name": "dup"}).status_code == 409
    assert client.post("/operators", json={"name": "bootstrap"}).status_code == 409


def test_bootstrap_cannot_be_revoked_through_the_api(client):
    bootstrap = client.get("/operators/me").json()
    assert client.delete(f"/operators/{bootstrap['id']}").status_code == 400


def test_removing_the_env_key_locks_bootstrap_but_not_minted_operators(client):
    token = _mint_operator(client, "survivor")
    settings.fleet_operator_key = None
    try:
        assert client.get("/operators/me").status_code == 403  # the old env key
        assert client.get("/operators/me", headers=_bearer(token)).status_code == 200
    finally:
        settings.fleet_operator_key = OPERATOR_KEY


def test_rollout_records_the_verified_starter(client):
    token = _mint_operator(client, "starter")
    started, _ = _start_rollout_for_one_installation(client, headers=_bearer(token))
    assert started["started_by"] == "starter"
