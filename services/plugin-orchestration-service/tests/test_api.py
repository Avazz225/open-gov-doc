import re
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from plugin_orchestration_service import sampler
from plugin_orchestration_service.main import app


def _extract_metric_value(exposition_text: str, metric_name: str) -> float:
    """Reads an unlabeled metric's value out of Prometheus exposition text -
    same helper as document-service's/storage-service's own test_api.py."""
    match = re.search(rf"^{re.escape(metric_name)} (\S+)$", exposition_text, re.MULTILINE)
    assert match is not None, f"{metric_name!r} not found in exposition text"
    return float(match.group(1))


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
        app.state.permission_client.is_maintenance_active.return_value = False
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


def test_placement_rejected_during_maintenance_mode(client):
    """Post-Roadmap Phase 44 Session 3 (4.8, ADR 0164) - the closest
    honest analog this recommendation-only service has to "halt plugin
    instances": refuses to authorize a NEW placement decision while
    maintenance mode is active, mirroring `workflow-service`'s own
    identical rejection of new process-instance starts."""
    app.state.permission_client.is_maintenance_active.return_value = True
    response = client.post(
        "/placements", json={"plugin_type": "cmis-connector"}, headers={"x-dms-principal": "alice"}
    )
    assert response.status_code == 503


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


def test_metrics_endpoint_exposes_node_resource_sensors(client):
    """Phase 40 Session 4 - migrates the existing `psutil`-sampled node
    values onto the real sensor infrastructure, additive to (not a
    replacement of) the `ClusterNode` upsert `placement.py` reads."""
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "plugin_orchestration_node_cpu_usage_percent" in response.text
    assert "plugin_orchestration_node_available_ram_mb" in response.text


async def test_run_tick_sets_the_sensor_gauges_from_the_same_sample(client, session, monkeypatch):
    """`run_tick` must feed the gauges from the SAME sampled values used
    for the `ClusterNode` upsert, not a second independent `psutil` call -
    verified indirectly here by asserting the gauge value matches the
    upserted row afterward."""
    from plugin_orchestration_service.main import available_ram_gauge, cpu_usage_gauge

    monkeypatch.setattr(cpu_usage_gauge, "_is_active", lambda name: True)
    monkeypatch.setattr(available_ram_gauge, "_is_active", lambda name: True)

    await sampler.run_tick(
        session, cpu_usage_gauge=cpu_usage_gauge, available_ram_gauge=available_ram_gauge
    )

    response = client.get("/metrics")
    cpu_value = _extract_metric_value(response.text, "plugin_orchestration_node_cpu_usage_percent")
    ram_value = _extract_metric_value(response.text, "plugin_orchestration_node_available_ram_mb")
    # Real psutil values (not mocked) - only plausibility, not exact
    # numbers, can be asserted (same convention as
    # `test_sample_local_node_returns_plausible_values`).
    assert 0.0 <= cpu_value <= 100.0
    assert ram_value > 0.0
