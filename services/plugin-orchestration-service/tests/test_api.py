from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from plugin_orchestration_service import sampler
from plugin_orchestration_service.main import app


@pytest.fixture
def client():
    """Externe Clients durch AsyncMock ersetzt - identisches Muster wie
    license-service's `test_api.py`-Fixture. `app.state.event_bus` bleibt der
    echte, in der Lifespan verbundene `NatsEventBusClient` (kein Mock)."""
    with TestClient(app) as c:
        app.state.auth_client = AsyncMock()
        app.state.auth_client.get_active_superuser.return_value = (False, None)
        app.state.permission_client = AsyncMock()
        app.state.permission_client.has_permission.return_value = True
        app.state.registry_client = AsyncMock()
        app.state.registry_client.has_healthy_instance.return_value = True
        yield c


async def _seed_node(session) -> None:
    """Nutzt die `session`-Fixture (eigene, ans aktuelle Test-Event-Loop
    gebundene Engine, siehe `conftest.py`) statt `app.state.session_factory`
    - dessen Engine gehoert TestClients eigenem Portal-Thread-Loop, eine
    fremde Session-Factory ueber Event-Loop-Grenzen hinweg zu verwenden
    fuehrt zu "attached to a different loop"-Fehlern. Beide Engines zeigen
    auf dieselbe Test-Datenbank, daher reicht ein `COMMIT` hier, damit
    TestClients Requests die Zeile sehen. Nutzt denselben atomaren Upsert wie
    der echte Hintergrund-Sampler (`sampler.upsert_node`) statt eines eigenen
    Get-dann-Insert - der Hintergrund-Loop laeuft waehrend der Tests parallel
    weiter und wuerde sonst in eine echte `UniqueViolationError`-Race laufen
    (siehe `sampler.upsert_node`-Docstring)."""
    await sampler.upsert_node(
        session,
        {
            "cpu_cores": 4.0,
            "total_ram_mb": 8192.0,
            "cpu_usage_percent": 0.0,
            "available_ram_mb": 8192.0,
            "sampled_at": datetime.now(UTC),
        },
    )


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "plugin-orchestration-service"


def test_upsert_manifest_requires_principal_header(client):
    response = client.post(
        "/plugins/cmis-connector",
        json={"version": "1.0.0", "scaling_type": "stateless_horizontal"},
    )
    assert response.status_code == 403


def test_upsert_manifest_requires_orchestration_permission(client):
    app.state.permission_client.has_permission.return_value = False
    response = client.post(
        "/plugins/cmis-connector",
        json={"version": "1.0.0", "scaling_type": "stateless_horizontal"},
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 403


def test_upsert_manifest_succeeds_and_is_listed(client):
    response = client.post(
        "/plugins/cmis-connector",
        json={
            "version": "1.0.0",
            "scaling_type": "stateless_horizontal",
            "resource_cpu_cores": 1.0,
            "resource_ram_mb": 512.0,
            "dependencies": ["storage-service"],
        },
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 201
    assert response.json()["plugin_type"] == "cmis-connector"

    list_response = client.get("/plugins")
    assert list_response.status_code == 200
    assert any(m["plugin_type"] == "cmis-connector" for m in list_response.json())

    get_response = client.get("/plugins/cmis-connector")
    assert get_response.status_code == 200


def test_get_unknown_manifest_returns_404(client):
    response = client.get("/plugins/does-not-exist")
    assert response.status_code == 404


def test_report_resource_usage_is_ungated(client):
    response = client.post(
        "/plugins/cmis-connector/resource-usage",
        json={"instance_id": "cmis-connector-abc123", "cpu_cores": 0.5, "ram_mb": 128.0},
    )
    assert response.status_code == 204


def test_placement_requires_orchestration_permission(client):
    app.state.permission_client.has_permission.return_value = False
    response = client.post(
        "/placements", json={"plugin_type": "cmis-connector"}, headers={"x-dms-principal": "alice"}
    )
    assert response.status_code == 403


def test_placement_for_unknown_manifest_returns_404(client):
    response = client.post(
        "/placements", json={"plugin_type": "does-not-exist"}, headers={"x-dms-principal": "alice"}
    )
    assert response.status_code == 404


async def test_placement_decision_is_created_and_listed(client, session):
    await _seed_node(session)
    client.post(
        "/plugins/cmis-connector",
        json={
            "version": "1.0.0",
            "scaling_type": "stateless_horizontal",
            "resource_cpu_cores": 1.0,
            "resource_ram_mb": 512.0,
        },
        headers={"x-dms-principal": "alice"},
    )

    response = client.post(
        "/placements", json={"plugin_type": "cmis-connector"}, headers={"x-dms-principal": "alice"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source"] == "manifest"
    assert body["placement_allowed"] is True
    assert body["node_id"] == "self"
    assert body["placement_method"] == "ffd"

    list_response = client.get("/placements?plugin_type=cmis-connector")
    assert list_response.status_code == 200
    assert len(list_response.json()) == 1


async def test_singleton_second_placement_conflicts(client, session):
    await _seed_node(session)
    client.post(
        "/plugins/signature-connector",
        json={
            "version": "1.0.0",
            "scaling_type": "singleton",
            "resource_cpu_cores": 0.5,
            "resource_ram_mb": 128.0,
        },
        headers={"x-dms-principal": "alice"},
    )
    client.post(
        "/plugins/signature-connector/resource-usage",
        json={"instance_id": "signature-connector-1", "cpu_cores": 0.5, "ram_mb": 128.0},
    )

    response = client.post(
        "/placements",
        json={"plugin_type": "signature-connector"},
        headers={"x-dms-principal": "alice"},
    )

    assert response.status_code == 409


def test_nodes_endpoint_returns_list(client):
    response = client.get("/nodes")
    assert response.status_code == 200
    assert isinstance(response.json(), list)


def test_upsert_node_requires_principal_header(client):
    response = client.post(
        "/nodes/remote-node-1",
        json={"cpu_cores": 8.0, "total_ram_mb": 16384.0},
    )
    assert response.status_code == 403


def test_upsert_node_requires_orchestration_permission(client):
    app.state.permission_client.has_permission.return_value = False
    response = client.post(
        "/nodes/remote-node-1",
        json={"cpu_cores": 8.0, "total_ram_mb": 16384.0},
        headers={"x-dms-principal": "alice"},
    )
    assert response.status_code == 403


def test_upsert_node_succeeds_and_is_listed(client):
    response = client.post(
        "/nodes/remote-node-1",
        json={"cpu_cores": 8.0, "total_ram_mb": 16384.0},
        headers={"x-dms-principal": "alice"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["node_id"] == "remote-node-1"
    assert body["cpu_cores"] == 8.0
    # Kein `available_ram_mb` mitgeschickt -> Default ist "voll verfuegbar".
    assert body["available_ram_mb"] == 16384.0

    list_response = client.get("/nodes")
    assert any(n["node_id"] == "remote-node-1" for n in list_response.json())
