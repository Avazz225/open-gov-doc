import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dms_auth_client import InvalidTokenError, MultiIssuerTokenValidator, TokenValidator
from dms_common import configure_logging
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_registry_client import maybe_start_registration
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from gateway_service.rate_limiter import RateLimiter
from gateway_service.settings import Settings
from gateway_service.upstream import InstanceResolver, MaintenanceStateClient, filter_headers

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


# Auth decoupling from Keycloak (Phase 18, ADR 0063): must exactly match
# `auth_service.local_token_issuer.LOCAL_ISSUER`. Deliberately duplicated as
# its own string instead of a new shared-lib constant - no `libs/` package
# knows this value yet, `gateway-service` (like every other service)
# doesn't import any code from `auth-service` itself anyway.
_LOCAL_TOKEN_ISSUER = "dms-auth-service-local"


def _build_token_validator(settings: Settings) -> MultiIssuerTokenValidator:
    issuer = f"{settings.keycloak_base_url}/realms/{settings.keycloak_realm}"
    keycloak_validator = TokenValidator(
        issuer=issuer,
        audience=settings.keycloak_client_id,
        jwks_url=f"{issuer}/protocol/openid-connect/certs",
    )
    local_validator = TokenValidator(
        issuer=_LOCAL_TOKEN_ISSUER,
        audience=settings.keycloak_client_id,
        jwks_url=f"{settings.auth_service_base_url}/.well-known/jwks.json",
    )
    return MultiIssuerTokenValidator([keycloak_validator, local_validator])


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    http_client = httpx.AsyncClient(timeout=settings.upstream_timeout_seconds)
    app.state.http_client = http_client
    app.state.instance_resolver = InstanceResolver(
        client=http_client,
        registry_base_url=settings.registry_service_base_url,
        cache_ttl_seconds=settings.instance_cache_ttl_seconds,
    )
    app.state.rate_limiter = RateLimiter(
        redis_url=settings.redis_url,
        max_requests=settings.rate_limit_max_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    app.state.maintenance_state = MaintenanceStateClient(
        client=http_client,
        resolver=app.state.instance_resolver,
        cache_ttl_seconds=settings.maintenance_cache_ttl_seconds,
    )
    # On app.state instead of a module constant, so tests can inject an
    # instance that validates against local test keys, without needing a
    # real Keycloak (analogous to libs/dms-auth-client/tests).
    app.state.token_validator = _build_token_validator(settings)

    # Sensor concept (10.1, full rollout): a fresh `SensorConfigClient` per
    # startup, bound into the module-level `sensor_config_proxy` (its httpx
    # client can't outlive the event loop it was first used on, see
    # `SensorConfigProxy`'s docstring) - not a module-level client itself.
    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    # Self-registration (3.2a), new for gateway-service (10.1 full rollout):
    # unlike every other service, nothing needs to *discover* the gateway
    # (it's the fixed entry point everything else talks through, not a
    # load-balanced backend) - this exists purely so monitoring-service's
    # scrape-proxy can find and scrape this instance's new `/metrics`
    # endpoint. Fails open (returns None, no registration attempted) unless
    # both `registry_service_base_url`/`self_address` are configured, same
    # as every other service.
    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        sensors=http_sensor_declarations(),
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    await http_client.aclose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

# Sensor concept (10.1, full rollout): must run at module level, right
# after `app` is constructed - see bootstrap_http_sensors's docstring
# for why this can't move into `lifespan` (FastAPI forbids adding
# middleware once the app has started).
sensor_config_proxy, sensor_registry, _http_requests_sensor, _http_duration_sensor = (
    bootstrap_http_sensors(app, settings.service_name)
)

# Must be registered before the routes, so Starlette intercepts preflight
# OPTIONS requests before they would hit the generic proxy route (OPTIONS
# is not even registered as a method there).
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


def _extract_bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Fehlender Bearer-Token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.split(" ", 1)[1]


@app.api_route("/api/{service_type}/{path:path}", methods=_PROXY_METHODS)
async def proxy(service_type: str, path: str, request: Request) -> Response:
    route_key = f"{service_type}:{path}"
    client_host = request.client.host if request.client else "unknown"
    rate_limit_key = client_host
    identity_headers: dict[str, str] = {}

    # Emergency shutdown (4.8, P6-S6): single central enforcement point,
    # since every proxied request passes through this function. A small
    # allow list remains reachable (login/refresh/me/superuser status, the
    # two maintenance-mode endpoints themselves) - `auth-service` also
    # rejects logins server-side for anyone except the superuser (see
    # below).
    maintenance_active = await request.app.state.maintenance_state.is_active()
    if maintenance_active and route_key not in settings.maintenance_mode_allowed_routes:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Systemweite Notfallsperre aktiv - Wartungsmodus",
        )

    if route_key not in settings.public_routes:
        token = _extract_bearer_token(request.headers.get("authorization"))
        try:
            claims = request.app.state.token_validator.validate(token)
        except InvalidTokenError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
                headers={"WWW-Authenticate": "Bearer"},
            ) from exc
        rate_limit_key = claims.get("sub", client_host)
        identity_headers = {
            "X-DMS-Principal": claims.get("sub", ""),
            "X-DMS-Username": claims.get("preferred_username", ""),
            "X-DMS-Roles": ",".join(claims.get("realm_access", {}).get("roles", [])),
        }

    if not await request.app.state.rate_limiter.allow(rate_limit_key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Rate limit überschritten"
        )

    resolver: InstanceResolver = request.app.state.instance_resolver
    instances = await resolver.resolve(service_type)
    if not instances:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Kein aktiver Dienst für service_type={service_type!r} registriert",
        )

    body = await request.body()
    headers = filter_headers(request.headers)
    headers.update(identity_headers)
    # Broadcast instead of polling (ADR 0024): every backend service that
    # requests get through can evaluate the maintenance-mode status without
    # establishing its own connection to permission-service (e.g.
    # auth-service `POST /login`).
    headers["X-DMS-Maintenance-Active"] = "true" if maintenance_active else "false"

    # Workload-aware instance selection (P25-S4): `reserved_instance()`
    # picks the instance with the fewest currently open requests and keeps
    # the counter open for the entire duration of the upstream call - the
    # `finally` in `reserved_instance()` releases the slot again even on an
    # `httpx.HTTPError` below, no permanent leaking.
    async with resolver.reserved_instance(instances) as instance:
        try:
            upstream_response = await request.app.state.http_client.request(
                request.method,
                f"{instance['address'].rstrip('/')}/{path}",
                params=request.query_params,
                headers=headers,
                content=body,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Upstream-Fehler: {exc}"
            ) from exc

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=filter_headers(upstream_response.headers),
        media_type=upstream_response.headers.get("content-type"),
    )
