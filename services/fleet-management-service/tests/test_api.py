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
