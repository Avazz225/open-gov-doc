import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dms_auth_client import InvalidTokenError, TokenValidator
from dms_common import configure_logging
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from gateway_service.rate_limiter import RateLimiter
from gateway_service.settings import Settings
from gateway_service.upstream import InstanceResolver, MaintenanceStateClient, filter_headers

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def _build_token_validator(settings: Settings) -> TokenValidator:
    issuer = f"{settings.keycloak_base_url}/realms/{settings.keycloak_realm}"
    return TokenValidator(
        issuer=issuer,
        audience=settings.keycloak_client_id,
        jwks_url=f"{issuer}/protocol/openid-connect/certs",
    )


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
        max_requests=settings.rate_limit_max_requests,
        window_seconds=settings.rate_limit_window_seconds,
    )
    app.state.maintenance_state = MaintenanceStateClient(
        client=http_client,
        resolver=app.state.instance_resolver,
        cache_ttl_seconds=settings.maintenance_cache_ttl_seconds,
    )
    # Auf app.state statt Modulkonstante, damit Tests eine gegen lokale
    # Test-Schlüssel validierende Instanz einsetzen können, ohne einen echten
    # Keycloak zu benötigen (analog zu libs/dms-auth-client/tests).
    app.state.token_validator = _build_token_validator(settings)

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    await http_client.aclose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

# Muss vor den Routen registriert werden, damit Starlette Preflight-
# OPTIONS-Requests abfängt, bevor sie auf die generische Proxy-Route treffen
# würden (dort ist OPTIONS gar nicht als Methode registriert).
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

    # Not-Shutdown (4.8, P6-S6): einziger zentraler Durchsetzungspunkt, da
    # jeder proxied Request diese Funktion durchläuft. Eine kleine Allow-Liste
    # bleibt erreichbar (Login/Refresh/Me/Superuser-Status, die beiden
    # Wartungsmodus-Endpunkte selbst) - `auth-service` lehnt Logins für jeden
    # außer den Superuser selbst zusätzlich serverseitig ab (siehe unten).
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

    if not request.app.state.rate_limiter.allow(rate_limit_key):
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
    instance = resolver.pick(instances)

    body = await request.body()
    headers = filter_headers(request.headers)
    headers.update(identity_headers)
    # Broadcast statt Polling (ADR 0024): jeder durchgelassene Backend-Service
    # kann den Wartungsmodus-Status auswerten, ohne selbst eine Verbindung zu
    # permission-service aufzubauen (z. B. auth-service `POST /login`).
    headers["X-DMS-Maintenance-Active"] = "true" if maintenance_active else "false"

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
