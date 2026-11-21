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


# --- P88-S2 (ADR 0243): group and cost-class overrides ------------------------


def _instance_with(*sensors: dict) -> RegistryInstance:
    return RegistryInstance(
        "svc-1", "storage-service", "http://storage-service:8000", list(sensors)
    )


def _sensor(name: str, group: str, cost: str) -> dict:
    return {"name": name, "group": group, "cost": cost, "description": name}


def _put(client, key: str, enabled):
    return client.put(
        f"/sensor-config/{key}", json={"enabled": enabled}, headers={"x-dms-principal": "alice"}
    )


def test_catalog_resolves_group_and_cost_overrides_and_names_the_source(client):
    app.state.registry_client.list_active_instances.return_value = [
        _instance_with(
            _sensor("storage.backlog", "capacity", "cheap"),
            _sensor("storage.scan", "capacity", "expensive"),
            _sensor("doc.upload", "performance", "expensive"),
            _sensor("doc.count", "performance", "cheap"),
        )
    ]
    try:
        assert _put(client, "cost:expensive", False).status_code == 200
        assert _put(client, "group:capacity", True).status_code == 200
        assert _put(client, "doc.count", False).status_code == 200

        body = {s["name"]: (s["active"], s["source"]) for s in client.get("/sensors").json()}
        assert body == {
            "storage.backlog": (True, "group"),
            "storage.scan": (True, "group"),  # group beats cost
            "doc.upload": (False, "cost"),
            "doc.count": (False, "sensor"),
        }
        # The catalog comes sorted by group, then name.
        assert [s["name"] for s in client.get("/sensors").json()][0] == "storage.backlog"
    finally:
        for key in ("cost:expensive", "group:capacity", "doc.count"):
            _put(client, key, None)


def test_unknown_prefixed_keys_are_refused(client):
    assert _put(client, "cost:medium", False).status_code == 422
    assert _put(client, "grp:storage", False).status_code == 422
    assert _put(client, "group:", False).status_code == 422


# --- P88-S2c: cost levels 1-5 and default-off sensors -------------------------


def test_cost_levels_legacy_keys_and_default_off_sensors(client):
    app.state.registry_client.list_active_instances.return_value = [
        _instance_with(
            {**_sensor("cheap.one", "capacity", 1)},
            {**_sensor("legacy.expensive", "performance", "expensive")},
            {**_sensor("breakdown", "capacity", 5), "default_enabled": False},
        )
    ]
    try:
        body = {s["name"]: s for s in client.get("/sensors").json()}
        # An old registration's "expensive" is shown as level 4.
        assert body["legacy.expensive"]["cost"] == 4
        assert body["breakdown"]["default_enabled"] is False
        assert (body["breakdown"]["active"], body["breakdown"]["source"]) == (False, "default")

        # A legacy key is written as its levels.
        written = _put(client, "cost:expensive", False).json()["overrides"]
        assert written == {"cost:4": False, "cost:5": False}
        assert _put(client, "cost:9", False).status_code == 422

        # Level 5 on switches the default-off breakdown on.
        _put(client, "cost:5", True)
        body = {s["name"]: s for s in client.get("/sensors").json()}
        assert (body["breakdown"]["active"], body["breakdown"]["source"]) == (True, "cost")
        assert (body["legacy.expensive"]["active"], body["legacy.expensive"]["source"]) == (
            False,
            "cost",
        )
    finally:
        for key in ("cost:4", "cost:5"):
            _put(client, key, None)
