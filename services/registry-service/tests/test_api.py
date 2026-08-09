import uuid

import pytest
from fastapi.testclient import TestClient
from registry_service.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def make_payload(**overrides) -> dict:
    payload = {
        "instance_id": f"test-{uuid.uuid4().hex[:8]}",
        "service_type": "document-service",
        "version": "0.1.0",
        "capabilities": ["read", "write"],
        "health_endpoint": "http://doc-1:8000/healthz",
        "address": "http://doc-1:8000",
    }
    payload.update(overrides)
    return payload


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["service"] == "registry-service"


def test_get_installation(client):
    """3a/P13-S1: reine Konfigurationswerte, kein Auth-Gate nötig."""
    response = client.get("/installation")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "local-dev"
    assert body["display_name"] == "DMS-Installation (Entwicklung)"


def test_register_and_list_active(client):
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    payload = make_payload(service_type=service_type)

    register_response = client.post("/instances", json=payload)
    assert register_response.status_code == 201
    assert register_response.json()["healthy"] is True

    list_response = client.get(f"/instances/{service_type}")
    assert list_response.status_code == 200
    ids = {i["instance_id"] for i in list_response.json()}
    assert payload["instance_id"] in ids


def test_register_with_sensor_declarations_roundtrips(client):
    payload = make_payload(
        sensors=[
            {
                "name": "document.count.active_total",
                "group": "capacity",
                "cost": "cheap",
                "description": "Anzahl aktiver Dokumente",
            }
        ]
    )
    response = client.post("/instances", json=payload)
    assert response.status_code == 201
    assert response.json()["sensors"] == payload["sensors"]


def test_metrics_endpoint_exposes_own_pilot_sensors(client):
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "registry_instances_active_total" in response.text
    assert "registry_service_heartbeat_miss" in response.text


def test_heartbeat_unknown_instance_returns_404(client):
    response = client.post("/instances/does-not-exist/heartbeat")
    assert response.status_code == 404


def test_heartbeat_known_instance(client):
    payload = make_payload()
    client.post("/instances", json=payload)

    response = client.post(f"/instances/{payload['instance_id']}/heartbeat")

    assert response.status_code == 200
    assert response.json()["instance_id"] == payload["instance_id"]


def test_new_instance_registers_as_active(client):
    response = client.post("/instances", json=make_payload())
    assert response.json()["status"] == "active"


def test_drain_sets_status_to_draining(client):
    payload = make_payload()
    client.post("/instances", json=payload)

    response = client.post(f"/instances/{payload['instance_id']}/drain")

    assert response.status_code == 200
    assert response.json()["status"] == "draining"


def test_drain_unknown_instance_returns_404(client):
    response = client.post("/instances/does-not-exist/drain")
    assert response.status_code == 404


def test_draining_instance_still_listed(client):
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    payload = make_payload(service_type=service_type)
    client.post("/instances", json=payload)
    client.post(f"/instances/{payload['instance_id']}/drain")

    list_response = client.get(f"/instances/{service_type}")

    ids_to_status = {i["instance_id"]: i["status"] for i in list_response.json()}
    assert ids_to_status[payload["instance_id"]] == "draining"


def test_reregistering_same_instance_does_not_reset_draining(client):
    payload = make_payload()
    client.post("/instances", json=payload)
    client.post(f"/instances/{payload['instance_id']}/drain")

    response = client.post("/instances", json=payload)

    assert response.json()["status"] == "draining"


def test_activate_resets_draining_to_active(client):
    payload = make_payload()
    client.post("/instances", json=payload)
    client.post(f"/instances/{payload['instance_id']}/drain")

    response = client.post(f"/instances/{payload['instance_id']}/activate")

    assert response.status_code == 200
    assert response.json()["status"] == "active"


def test_activate_unknown_instance_returns_404(client):
    response = client.post("/instances/does-not-exist/activate")
    assert response.status_code == 404


def test_deregister_removes_instance(client):
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    payload = make_payload(service_type=service_type)
    client.post("/instances", json=payload)

    delete_response = client.delete(f"/instances/{payload['instance_id']}")
    assert delete_response.status_code == 204

    list_response = client.get(f"/instances/{service_type}")
    ids = {i["instance_id"] for i in list_response.json()}
    assert payload["instance_id"] not in ids


def test_deregister_unknown_instance_returns_404(client):
    response = client.delete("/instances/does-not-exist")
    assert response.status_code == 404


def test_register_response_includes_license_status_for_core_service(client):
    response = client.post("/instances", json=make_payload())
    assert response.status_code == 201
    # "document-service" ist keine licensierbare Komponente (default
    # licensable_components nur "workflow-service"/"webdav-connector") -
    # immer "licensed".
    assert response.json()["license_status"] == "licensed"


def test_license_status_endpoint_for_licensable_component_uses_configured_policy(client):
    class FakeCache:
        async def status_for(self, service_type: str) -> str:
            assert service_type == "workflow-service"
            return "demo"

        async def close(self) -> None:
            pass

    app.state.license_cache = FakeCache()

    response = client.get("/license-status/workflow-service")

    assert response.status_code == 200
    assert response.json() == {"service_type": "workflow-service", "status": "demo"}


def test_heartbeat_response_includes_license_status(client):
    payload = make_payload()
    client.post("/instances", json=payload)

    response = client.post(f"/instances/{payload['instance_id']}/heartbeat")

    assert response.json()["license_status"] == "licensed"
