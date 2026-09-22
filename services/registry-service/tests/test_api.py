import uuid

import pytest
from fastapi.testclient import TestClient
from registry_service.main import app
from registry_service.main import settings as registry_settings

# Muss mit dem `Authorization: Bearer ...`-Header übereinstimmen, den die
# Tests unten für /drain und /activate setzen - kein Cross-File-Import von
# Test-Konstanten, gleiche Projektkonvention wie andernorts.
_OPERATOR_KEY = "operator-secret-for-test"


@pytest.fixture
def client():
    """Default `X-DMS-Principal: document-service` (Phase 59 Session 4) -
    matches `make_payload`'s own default `service_type`, so every existing
    call site using the default payload already satisfies the new
    self-service gate with no per-call header needed. Tests that pick a
    different `service_type` pass a matching header explicitly (same
    pattern as `real_signer` overrides elsewhere in this project)."""
    with TestClient(app, headers={"X-DMS-Principal": "document-service"}) as c:
        yield c


@pytest.fixture
def operator_key():
    """Enables the drain/activate operator gate (Phase 59 Session 4) for a
    single test, then resets it - same mutate-then-reset pattern as
    `federation-hub-service`'s `hub_operator_key` tests, since `Settings()`
    is a shared module-level singleton, not re-created per test."""
    registry_settings.registry_operator_key = _OPERATOR_KEY
    try:
        yield {"Authorization": f"Bearer {_OPERATOR_KEY}"}
    finally:
        registry_settings.registry_operator_key = None


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

    register_response = client.post(
        "/instances", json=payload, headers={"X-DMS-Principal": service_type}
    )
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


def test_drain_sets_status_to_draining(client, operator_key):
    payload = make_payload()
    client.post("/instances", json=payload)

    response = client.post(f"/instances/{payload['instance_id']}/drain", headers=operator_key)

    assert response.status_code == 200
    assert response.json()["status"] == "draining"


def test_drain_without_operator_key_is_403(client):
    """RBAC (Phase 59 Session 4) - `/drain` previously had no gate at all;
    this proves it now actually fires, same coverage shape as the sibling
    P59-S1/S2/S3 sessions' own `.._without_permission_is_403` tests."""
    payload = make_payload()
    client.post("/instances", json=payload)

    response = client.post(f"/instances/{payload['instance_id']}/drain")

    assert response.status_code == 403


def test_drain_unknown_instance_returns_404(client, operator_key):
    response = client.post("/instances/does-not-exist/drain", headers=operator_key)
    assert response.status_code == 404


def test_draining_instance_still_listed(client, operator_key):
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    payload = make_payload(service_type=service_type)
    client.post("/instances", json=payload, headers={"X-DMS-Principal": service_type})
    client.post(f"/instances/{payload['instance_id']}/drain", headers=operator_key)

    list_response = client.get(f"/instances/{service_type}")

    ids_to_status = {i["instance_id"]: i["status"] for i in list_response.json()}
    assert ids_to_status[payload["instance_id"]] == "draining"


def test_reregistering_same_instance_does_not_reset_draining(client, operator_key):
    payload = make_payload()
    client.post("/instances", json=payload)
    client.post(f"/instances/{payload['instance_id']}/drain", headers=operator_key)

    response = client.post("/instances", json=payload)

    assert response.json()["status"] == "draining"


def test_activate_resets_draining_to_active(client, operator_key):
    payload = make_payload()
    client.post("/instances", json=payload)
    client.post(f"/instances/{payload['instance_id']}/drain", headers=operator_key)

    response = client.post(f"/instances/{payload['instance_id']}/activate", headers=operator_key)

    assert response.status_code == 200
    assert response.json()["status"] == "active"


def test_activate_without_operator_key_is_403(client, operator_key):
    payload = make_payload()
    client.post("/instances", json=payload)
    client.post(f"/instances/{payload['instance_id']}/drain", headers=operator_key)

    response = client.post(f"/instances/{payload['instance_id']}/activate")

    assert response.status_code == 403


def test_activate_unknown_instance_returns_404(client, operator_key):
    response = client.post("/instances/does-not-exist/activate", headers=operator_key)
    assert response.status_code == 404


def test_deregister_removes_instance(client):
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    payload = make_payload(service_type=service_type)
    client.post("/instances", json=payload, headers={"X-DMS-Principal": service_type})

    delete_response = client.delete(
        f"/instances/{payload['instance_id']}", headers={"X-DMS-Principal": service_type}
    )
    assert delete_response.status_code == 204

    list_response = client.get(f"/instances/{service_type}")
    ids = {i["instance_id"] for i in list_response.json()}
    assert payload["instance_id"] not in ids


def test_deregister_unknown_instance_returns_404(client):
    response = client.delete("/instances/does-not-exist")
    assert response.status_code == 404


def test_deregister_with_wrong_principal_is_403(client):
    """RBAC (Phase 59 Session 4) - a caller may only deregister an instance
    of its OWN `service_type`, not an arbitrary one - previously any
    authenticated caller could deregister any instance, a fleet-wide DoS
    vector."""
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    payload = make_payload(service_type=service_type)
    client.post("/instances", json=payload, headers={"X-DMS-Principal": service_type})

    response = client.delete(f"/instances/{payload['instance_id']}")

    assert response.status_code == 403


def test_register_without_principal_header_is_401(client):
    response = client.post("/instances", json=make_payload(), headers={"X-DMS-Principal": ""})
    assert response.status_code == 401


def test_register_with_mismatched_principal_is_403(client):
    """RBAC (Phase 59 Session 4) - `X-DMS-Principal` must equal the
    registered `service_type` - previously any caller could register a fake
    instance of an arbitrary `service_type` at an attacker-controlled
    `address`, a traffic-hijack vector via `gateway_service.upstream.
    InstanceResolver`."""
    response = client.post(
        "/instances",
        json=make_payload(service_type="storage-service"),
        headers={"X-DMS-Principal": "document-service"},
    )
    assert response.status_code == 403


def test_heartbeat_with_wrong_principal_is_403(client):
    service_type = f"type-{uuid.uuid4().hex[:8]}"
    payload = make_payload(service_type=service_type)
    client.post("/instances", json=payload, headers={"X-DMS-Principal": service_type})

    response = client.post(f"/instances/{payload['instance_id']}/heartbeat")

    assert response.status_code == 403


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


# --- Branding config (7.3/8, P69-S2, ADR 0201) -------------------------------


def test_get_branding_config_is_ungated_and_returns_a_value(client):
    """Ungated wie `GET /installation` - muss vor jedem Login lesbar sein."""
    response = client.get("/installation/branding")
    assert response.status_code == 200
    body = response.json()
    assert "product_name" in body
    assert "accent_color" in body
    assert "logo_url" in body
    assert body["updated_at"]


def test_update_branding_config_requires_admin(client):
    response = client.put("/installation/branding", json={"product_name": "Test"})
    assert response.status_code == 403


def test_update_branding_config_round_trip(client, config_admin_headers):
    """Stellt den vorherigen Stand am Ende wieder her (Singleton-Zeile,
    geteilt mit jedem anderen Test dieses Moduls), gleiches Muster wie
    `workflow-service`'s `test_update_federation_config_round_trip`."""
    previous = client.get("/installation/branding").json()
    try:
        response = client.put(
            "/installation/branding",
            json={
                "product_name": "P69-S2 Test Product",
                "accent_color": "#123456",
                "logo_url": "https://example.invalid/logo.png",
            },
            headers=config_admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["product_name"] == "P69-S2 Test Product"
        assert response.json()["accent_color"] == "#123456"
        assert response.json()["logo_url"] == "https://example.invalid/logo.png"

        get_response = client.get("/installation/branding")
        assert get_response.status_code == 200
        assert get_response.json()["product_name"] == "P69-S2 Test Product"
    finally:
        client.put(
            "/installation/branding",
            json={
                "product_name": previous["product_name"],
                "accent_color": previous["accent_color"],
                "logo_url": previous["logo_url"],
            },
            headers=config_admin_headers,
        )


def test_update_branding_config_can_unset_fields_back_to_none(client, config_admin_headers):
    """`None` bedeutet "Build-Standard verwenden", nicht "Fehler" - ein PUT
    mit `None`-Feldern muss einen zuvor gesetzten Wert wieder zurücksetzen
    können."""
    client.put(
        "/installation/branding",
        json={"product_name": "Temporary"},
        headers=config_admin_headers,
    )
    try:
        response = client.put(
            "/installation/branding",
            json={"product_name": None, "accent_color": None, "logo_url": None},
            headers=config_admin_headers,
        )
        assert response.status_code == 200
        assert response.json()["product_name"] is None
        assert response.json()["accent_color"] is None
        assert response.json()["logo_url"] is None
    finally:
        client.put(
            "/installation/branding",
            json={"product_name": None, "accent_color": None, "logo_url": None},
            headers=config_admin_headers,
        )
