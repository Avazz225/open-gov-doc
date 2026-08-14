# gateway-service

Central API gateway/BFF (Concept 3.5): the single publicly reachable
entry point that validates bearer tokens, enforces rate limiting, and
dynamically forwards requests to backend services via the registry, instead
of using hard-wired addresses.

## Routing convention

```
ANY /api/{service_type}/{path...}  →  {instance.address}/{path...}
```

`service_type` must match the `service_type` under which an instance
registered with the registry (e.g. `permission-service`,
`document-service`). The gateway queries `GET {registry}/instances/{service_type}`
(cached briefly, `instance_cache_ttl_seconds`), randomly picks a healthy
instance, and passes through method, query string, body, and headers (minus
hop-by-hop headers) unchanged.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `ANY` | `/api/{service_type}/{path:path}` | Proxy to an instance resolved via the registry |
| `GET` | `/healthz` | Own health check |

## Auth validation

Every proxied request requires a valid bearer token (JWT verified against
Keycloak JWKS, as in the Auth Service, 4.4) — **except** for routes in
`settings.public_routes` (default: `auth-service:login`, `auth-service:refresh`,
since you need a token in the first place to obtain one). On success, the
identity claims are forwarded to the downstream as headers:

| Header | Content |
|---|---|
| `X-DMS-Principal` | `sub` claim |
| `X-DMS-Username` | `preferred_username` claim |
| `X-DMS-Roles` | `realm_access.roles`, comma-separated |

Backend services do not yet consume these headers (every endpoint that
needs a principal, e.g. `permission-service`'s `/check`, still takes it
explicitly as a parameter) — wiring this up will follow once a UI/BFF
session actually needs these headers.

## Rate limiting

In-process sliding window per client (authenticated: `sub` claim, otherwise
client IP), `rate_limit_max_requests`/`rate_limit_window_seconds`. On
exceedance: `429`. Applies to **all** routes, including public ones - the
login endpoint itself must be protected against brute force.

**Known limitation**: purely local counter, no shared store. Once the
gateway is scaled horizontally, a client can simply bypass the limit via
multiple gateway instances (see [ADR 0005](../../docs/adr/0005-gateway-registry-routing-and-inprocess-rate-limiting.md)).

## Error cases

| Situation | Response |
|---|---|
| Missing/invalid bearer token on a protected route | `401` |
| Rate limit exceeded | `429` |
| No healthy target registered for `service_type` | `503` |
| Downstream unreachable/error | `502` |

## Self-registration

The gateway itself does **not** register with the registry - it is the
entry point that clients arrive at directly (fixed published port), not
something other services look up via the registry.

## Running locally

```bash
cd infra && docker compose up -d postgres nats keycloak registry-service gateway-service
curl localhost:8009/healthz
```

## Tests

Against real running infrastructure (no mocking of the registry/downstream;
JWT verification runs against real but locally generated test keys instead
of a real Keycloak, see `tests/conftest.py`):

```bash
cd infra && docker compose up -d postgres nats registry-service audit-service && cd ..
uv run pytest services/gateway-service/tests
```
