from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from monitoring_service.clients import RegistryInstance
from monitoring_service.main import app


@pytest.fixture
def client():
    """Externe Clients durch AsyncMock ersetzt - identisches Muster wie
    plugin-orchestration-service/license-service. `registry_client` steuert,
    welche Instanzen für `/sensors` und `/metrics` sichtbar sind."""
    with TestClient(app) as c:
        app.state.auth_client = AsyncMock()
        app.state.auth_client.get_active_superuser.return_value = (False, None)
        app.state.permission_client = AsyncMock()
        app.state.permission_client.has_permission.return_value = True
        app.state.registry_client = AsyncMock()
        app.state.registry_client.list_active_instances.return_value = []
        yield c


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "monitoring-service"


def test_metrics_endpoint_with_no_targets_still_exposes_own_failure_counter(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "monitoring_scrape_failures_total 0.0" in response.text


def test_sensors_endpoint_aggregates_declared_sensors(client):
    app.state.registry_client.list_active_instances.return_value = [
        RegistryInstance(
            "document-service-1",
            "document-service",
            "http://document-service:8000",
            [
                {
                    "name": "document.upload.duration",
                    "group": "performance",
                    "cost": "expensive",
                    "description": "Dauer eines Uploads",
                }
            ],
        )
    ]
    response = client.get("/sensors")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "document.upload.duration"
    assert body[0]["service_types"] == ["document-service"]
    assert body[0]["active"] is True


def test_sensor_config_write_requires_principal_header(client):
    response = client.put("/sensor-config/global", json={"enabled": False})
    assert response.status_code == 403


def test_sensor_config_write_requires_monitoring_permission(client):
    app.state.permission_client.has_permission.return_value = False
    response = client.put(
        "/sensor-config/global", json={"enabled": False}, headers={"x-dms-principal": "alice"}
    )
    assert response.status_code == 403


def test_sensor_config_global_write_and_read_roundtrip(client):
    response = client.put(
        "/sensor-config/global", json={"enabled": False}, headers={"x-dms-principal": "alice"}
    )
    assert response.status_code == 200
    assert response.json()["global_default"] is False

    read_back = client.get("/sensor-config")
    assert read_back.json()["global_default"] is False


def test_sensor_override_set_and_clear(client):
    set_response = client.put(
        "/sensor-config/some.sensor",
        json={"enabled": False},
        headers={"x-dms-principal": "alice"},
    )
    assert set_response.status_code == 200
    assert set_response.json()["overrides"]["some.sensor"] is False

    clear_response = client.put(
        "/sensor-config/some.sensor",
        json={"enabled": None},
        headers={"x-dms-principal": "alice"},
    )
    assert clear_response.status_code == 200
    assert "some.sensor" not in clear_response.json()["overrides"]


def test_superuser_bypasses_gate(client):
    app.state.auth_client.get_active_superuser.return_value = (True, "root-user")
    app.state.permission_client.has_permission.return_value = False
    response = client.put(
        "/sensor-config/global", json={"enabled": False}, headers={"x-dms-principal": "root-user"}
    )
    assert response.status_code == 200
