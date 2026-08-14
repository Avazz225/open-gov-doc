import os
import uuid

import httpx
import pytest
from dms_auth_client import TokenValidator
from fastapi.testclient import TestClient
from gateway_service.main import app
from gateway_service.rate_limiter import RateLimiter
from redis import asyncio as redis

REGISTRY_URL = os.environ.get("TEST_REGISTRY_SERVICE_URL", "http://localhost:8001")
# Echter Redis (seit P25-S3, siehe test_rate_limiter.py) statt eines
# in-process Dicts - dieselbe Instanz wie jeder andere Testlauf.
REDIS_URL = os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/0")
# audit-service ist Teil der Standard-Compose-Umgebung und dient hier nur als
# real erreichbares Ziel, um echtes Proxying zu verifizieren (kein Mock).
REAL_TARGET_URL = os.environ.get("TEST_AUDIT_SERVICE_URL", "http://localhost:8002")
# permission-service dient hier als echtes "Echo"-Ziel für den Header-
# Spoofing-Sicherheitstest unten (`POST /delegations` spiegelt den
# empfangenen `X-DMS-Principal` 1:1 in `delegator_principal_id`, siehe
# docs/services/permission-service.md).
PERMISSION_SERVICE_URL = os.environ.get("TEST_PERMISSION_SERVICE_URL", "http://localhost:8004")


@pytest.fixture
async def client(jwks, issuer, audience):
    # Redis wird in diesem Projekt ausschließlich vom Rate Limiter des
    # Gateways genutzt (P25-S3) - vor jedem Test flushen hält den geteilten
    # Zähler isoliert, auch wenn dieselbe Test-Identität (Client-IP
    # "testclient" bzw. `sub` "user-123" aus `make_token`) über mehrere
    # Testfunktionen und mehrere komplette Testläufe hinweg wiederverwendet
    # wird - anders als beim alten in-process `dict`, das bei jeder neuen
    # `RateLimiter()`-Instanz automatisch leer startete (siehe
    # test_rate_limiter.py für den direkten Redis-Store-Test).
    redis_client = redis.from_url(REDIS_URL)
    await redis_client.flushdb()
    await redis_client.aclose()

    with TestClient(app) as c:
        # Ersetzt den gegen echtes Keycloak konfigurierten Validator durch einen,
        # der dieselbe echte JWT-Prüflogik gegen lokale Test-Schlüssel ausführt
        # (siehe conftest.py) - kein echter Keycloak in diesem Testlauf nötig.
        app.state.token_validator = TokenValidator(issuer=issuer, audience=audience, jwks=jwks)
        app.state.rate_limiter = RateLimiter(
            redis_url=REDIS_URL, max_requests=120, window_seconds=60.0
        )
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


def test_cors_preflight_from_allowed_frontend_origin_succeeds(client):
    # Browser-Frontends senden vor dem eigentlichen Request einen
    # OPTIONS-Preflight (curl tut das nicht, deshalb blieb dieser Fall bei
    # den sonstigen curl-basierten Tests unentdeckt). Ohne CORSMiddleware
    # antwortet FastAPI hier mit 405, da OPTIONS auf der Proxy-Route nicht
    # registriert ist.
    response = client.options(
        "/api/auth-service/login",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_preflight_from_unknown_origin_is_rejected(client):
    response = client.options(
        "/api/auth-service/login",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in response.headers


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


def test_fleet_management_routes_bypass_gateway_auth_check(client):
    """3a/P13-S2: fleet-management-service hat keinen Keycloak-Token dieser
    Installation - die vier neuen Routen müssen ohne Bearer-Token durchgehen,
    die eigentliche Absicherung übernehmen registry-service/license-service/
    config-service selbst (ungegatete Reads bzw. `DMS_FLEET_AGENT_API_KEY`)."""
    for method, path in (
        ("GET", "/api/registry-service/installation"),
        ("GET", "/api/license-service/license/status"),
        ("POST", "/api/license-service/license"),
        ("POST", "/api/config-service/config/fleet-import"),
    ):
        response = client.request(method, path, json={})
        assert response.json().get("detail") != "Fehlender Bearer-Token"


def test_config_import_route_now_requires_gateway_auth_check(client):
    """P17-S1: `config-service:config/import` ist seit P17-S1 KEIN
    öffentlicher Pfad mehr (nur noch `config/fleet-import`) - vorher blieb
    der Gateway für JEDEN Aufruf dieses Pfads unvalidiert, wodurch echte,
    eingeloggte Admins nie ein `X-DMS-Principal` bekamen (siehe
    `gateway_service.settings.public_routes`-Kommentar)."""
    response = client.post("/api/config-service/config/import", json={})
    assert response.status_code == 401
    assert response.json()["detail"] == "Fehlender Bearer-Token"


def test_public_share_link_routes_bypass_gateway_auth_check(client):
    """4.2a/P14-S10: anonyme Betrachter eines Freigabelinks besitzen keinen
    Bearer-Token dieser Installation - das eigentliche Zugriffsgeheimnis ist
    der Token als Query-Parameter, den document-service selbst prüft (siehe
    dortiges `_resolve_active_share_link`)."""
    for path in ("public/share-links", "public/share-links/content"):
        response = client.get(f"/api/document-service/{path}", params={"token": "irrelevant"})
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


async def test_client_supplied_x_dms_principal_header_is_overridden_by_gateway(client, make_token):
    """Sicherheitsfund (P14-S11-Live-Verifikation, siehe ADR 0049): vor dem
    Fix in `upstream.filter_headers` konnte ein Client mit gültigem Bearer-
    Token einen eigenen `X-DMS-Principal`-Header mitschicken, der NICHT
    überschrieben wurde, sondern neben dem echten, vom Gateway aus dem JWT
    abgeleiteten Header beim Downstream ankam - permission-service (echtes,
    bereits laufendes Ziel, kein Mock) übernahm dabei den vom Client
    gesendeten Wert statt der echten `sub`-Claim. `POST /delegations`
    spiegelt `X-DMS-Principal` 1:1 in `delegator_principal_id` und dient
    hier als Echo-Endpunkt."""
    service_type = f"gw-test-permission-{uuid.uuid4().hex[:8]}"
    instance_id = await _register_instance(service_type, address=PERMISSION_SERVICE_URL)
    try:
        token = make_token(extra_claims={"sub": "real-principal-from-jwt"})
        response = client.post(
            f"/api/{service_type}/delegations",
            headers={
                "Authorization": f"Bearer {token}",
                "X-DMS-Principal": "attacker-spoofed-principal",
            },
            json={
                "deputy_principal_id": f"deputy-{uuid.uuid4().hex[:8]}",
                "starts_at": "2026-01-01T00:00:00Z",
                "ends_at": "2026-01-02T00:00:00Z",
            },
        )
        assert response.status_code == 201
        assert response.json()["delegator_principal_id"] == "real-principal-from-jwt"
    finally:
        await _deregister_instance(service_type, instance_id)


async def _drain_instance(instance_id: str) -> None:
    async with httpx.AsyncClient(base_url=REGISTRY_URL) as registry:
        response = await registry.post(f"/instances/{instance_id}/drain")
        response.raise_for_status()


async def test_draining_instance_is_excluded_from_routing(client, make_token):
    """Drain-Mechanismus (10.5/3.8, P10-S2): eine draining Instanz bleibt in
    der Registry sichtbar, darf aber keine neuen Anfragen mehr bekommen -
    einzige Instanz dieses service_type -> das Gateway muss `503` liefern,
    genau wie bei gar keiner registrierten Instanz."""
    service_type = f"gw-test-drain-{uuid.uuid4().hex[:8]}"
    instance_id = await _register_instance(service_type, address=REAL_TARGET_URL)
    try:
        await _drain_instance(instance_id)
        token = make_token()
        response = client.get(
            f"/api/{service_type}/healthz", headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 503
    finally:
        await _deregister_instance(service_type, instance_id)


class _StubMaintenanceState:
    """Ersetzt `app.state.maintenance_state` für kontrollierte Tests - genau
    wie `test_rate_limit_returns_429_after_threshold` unten `app.state.
    rate_limiter` ersetzt, statt echten Wartungsmodus auf dem geteilten,
    live laufenden `permission-service` zu aktivieren (dessen Zustand
    außerhalb dieses Testlaufs sichtbar bliebe, siehe PROGRESS.md "Tooling &
    Testing" - dieselbe Vorsicht wie beim NATS-/Keycloak-Sharing)."""

    def __init__(self, active: bool) -> None:
        self._active = active

    async def is_active(self) -> bool:
        return self._active


def test_maintenance_mode_blocks_non_allowlisted_routes(client, make_token):
    app.state.maintenance_state = _StubMaintenanceState(True)
    token = make_token()

    response = client.get(
        "/api/audit-service/healthz", headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Systemweite Notfallsperre aktiv - Wartungsmodus"


def test_maintenance_mode_allows_allowlisted_routes(client):
    app.state.maintenance_state = _StubMaintenanceState(True)

    response = client.post("/api/auth-service/login", json={"username": "x", "password": "y"})

    assert response.json().get("detail") != "Systemweite Notfallsperre aktiv - Wartungsmodus"


def test_rate_limit_returns_429_after_threshold(client):
    app.state.rate_limiter = RateLimiter(redis_url=REDIS_URL, max_requests=2, window_seconds=60.0)

    responses = [
        client.post("/api/auth-service/login", json={"username": "x", "password": "y"})
        for _ in range(3)
    ]

    assert responses[-1].status_code == 429
    assert responses[-1].json()["detail"] == "Rate limit überschritten"


async def test_rate_limit_state_survives_gateway_process_restart():
    """Kernpunkt von P25-S3 (siehe ADR 0097): der Zähler lebt in Redis, nicht
    im Gateway-Prozess - eine frische `RateLimiter`-Instanz (steht hier für
    einen neu gestarteten Gateway-Container/eine neue Replika) sieht den
    bereits von einer vorherigen Instanz verbrauchten Zähler weiter, statt
    bei 0 neu anzufangen."""
    redis_client = redis.from_url(REDIS_URL)
    await redis_client.flushdb()
    await redis_client.aclose()

    key = "restart-test-client"
    first_process_limiter = RateLimiter(redis_url=REDIS_URL, max_requests=2, window_seconds=60.0)
    assert await first_process_limiter.allow(key) is True
    assert await first_process_limiter.allow(key) is True
    await first_process_limiter.aclose()

    # Steht für den Neustart: komplett neue Instanz, kein geteilter
    # Python-Zustand mit der Instanz oben.
    second_process_limiter = RateLimiter(redis_url=REDIS_URL, max_requests=2, window_seconds=60.0)
    assert await second_process_limiter.allow(key) is False
    await second_process_limiter.aclose()
