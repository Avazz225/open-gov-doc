import os
import uuid

import httpx
import pytest
from dms_auth_client import TokenValidator
from fastapi.testclient import TestClient
from gateway_service.main import app
from gateway_service.rate_limiter import RateLimiter

REGISTRY_URL = os.environ.get("TEST_REGISTRY_SERVICE_URL", "http://localhost:8001")
# audit-service ist Teil der Standard-Compose-Umgebung und dient hier nur als
# real erreichbares Ziel, um echtes Proxying zu verifizieren (kein Mock).
REAL_TARGET_URL = os.environ.get("TEST_AUDIT_SERVICE_URL", "http://localhost:8002")


@pytest.fixture
def client(jwks, issuer, audience):
    with TestClient(app) as c:
        # Ersetzt den gegen echtes Keycloak konfigurierten Validator durch einen,
        # der dieselbe echte JWT-Prüflogik gegen lokale Test-Schlüssel ausführt
        # (siehe conftest.py) - kein echter Keycloak in diesem Testlauf nötig.
        app.state.token_validator = TokenValidator(issuer=issuer, audience=audience, jwks=jwks)
        app.state.rate_limiter = RateLimiter(max_requests=120, window_seconds=60.0)
        yield c


async def _register_instance(
    service_type: str, *, address: str, health_endpoint: str = "/healthz"
) -> str:
    instance_id = f"{service_type}-{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient(base_url=REGISTRY_URL) as registry:
        response = await registry.post(
            "/instances",
            json={
                "instance_id": instance_id,
                "service_type": service_type,
                "version": "0.1.0",
                "capabilities": [],
                "health_endpoint": health_endpoint,
                "address": address,
            },
        )
        response.raise_for_status()
    return instance_id


async def _deregister_instance(service_type: str, instance_id: str) -> None:
    async with httpx.AsyncClient(base_url=REGISTRY_URL) as registry:
        await registry.delete(f"/instances/{instance_id}")


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "gateway-service"}


def test_protected_route_without_token_is_rejected(client):
    response = client.get("/api/permission-service/healthz")
    assert response.status_code == 401
    assert response.json()["detail"] == "Fehlender Bearer-Token"


def test_protected_route_with_invalid_token_is_rejected(client):
    response = client.get(
        "/api/permission-service/healthz", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] != "Fehlender Bearer-Token"


def test_public_route_bypasses_gateway_auth_check(client):
    # Kein Authorization-Header nötig, weil "auth-service:login" in
    # settings.public_routes steht - Antwort kommt vom echten Downstream
    # (oder 503, falls kein auth-service registriert ist), nie der Gateway-
    # eigenen "Fehlender Bearer-Token"-Ablehnung.
    response = client.post("/api/auth-service/login", json={"username": "x", "password": "y"})
    assert response.json().get("detail") != "Fehlender Bearer-Token"


def test_no_healthy_instance_returns_503(client, make_token):
    token = make_token()
    response = client.get(
        f"/api/unknown-service-{uuid.uuid4().hex[:8]}/healthz",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 503


async def test_valid_token_routes_via_registry_to_real_instance(client, make_token):
    service_type = f"gw-test-target-{uuid.uuid4().hex[:8]}"
    instance_id = await _register_instance(service_type, address=REAL_TARGET_URL)
    try:
        token = make_token()
        response = client.get(
            f"/api/{service_type}/healthz", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok", "service": "audit-service"}
    finally:
        await _deregister_instance(service_type, instance_id)


def test_rate_limit_returns_429_after_threshold(client):
    app.state.rate_limiter = RateLimiter(max_requests=2, window_seconds=60.0)

    responses = [
        client.post("/api/auth-service/login", json={"username": "x", "password": "y"})
        for _ in range(3)
    ]

    assert responses[-1].status_code == 429
    assert responses[-1].json()["detail"] == "Rate limit überschritten"
