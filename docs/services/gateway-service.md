# gateway-service

**Responsibility:** Central API gateway/BFF — the sole intended public entry point: bearer-token validation, rate limiting, dynamic routing to backend services via the registry (concept 3.5). Since P6-S6, additionally the central enforcement point for the system-wide maintenance mode (emergency shutdown, 4.8) — see [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md).

**Concept Reference:** 3.5, 4.8
**Own Postgres Schema:** none (stateless)

## API

| Method | Path | Description |
|---|---|---|
| `ANY` | `/api/{service_type}/{path:path}` | Proxy to a healthy instance of `service_type` resolved via the registry |
| `GET` | `/healthz` | Own health check |

## Routing

Queries `GET {registry-service}/instances/{service_type}` (cache TTL
`instance_cache_ttl_seconds`, default 5s), picks one instance among the healthy,
**non-draining** instances (drain mechanism, 10.5/3.8, P10-S2 — a
`status="draining"` instance remains reachable but no longer receives new
requests, see `docs/services/registry-service.md`) (see
"Instance Selection / Load Balancing" below) and forwards the method, query
string, body, and headers (minus hop-by-hop headers: `Connection`,
`Keep-Alive`, `Transfer-Encoding`, `Host`, ...) unchanged to
`{instance.address}/{path}`. No registered/healthy/active target → `503`.
Downstream unreachable → `502`.

## Instance Selection / Load Balancing

**Since P25-S4 workload-aware instead of random** (previously `random.choice`,
ADR 0005; design decision see [ADR 0098](../adr/0098-gateway-workload-aware-instance-selection-per-replica.md)):
`InstanceResolver.pick()` selects, among the candidates returned by the
registry call, the instance with the **fewest currently open requests**
(instances with no prior reservation count as 0). `proxy()` reserves the
chosen instance for the duration of the upstream call via
`resolver.reserved_instance(instances)` — an async context manager that calls
`pick()`, increments the counter before the actual `http_client.request(...)`,
and releases it again in a `finally` block, i.e. **even if the upstream call
fails with an `httpx.HTTPError`** (no permanent leaking of a reserved slot).
Anyone wanting to control the counter manually outside this context manager
can alternatively use the two underlying methods `reserve(instance)`/
`release(instance)` directly.

**Tie-break when multiple instances share the same minimum**: random among
the minimum candidates, rather than e.g. always the first instance in the
list — particularly at rest (all counters at 0, e.g. right after startup),
"first in the list" would otherwise send every request to the same instance
until it is the first to have an open request, instead of spreading the load
evenly from the start.

**Important: purely per gateway replica, not a cluster-wide value.** The
open-request counter lives exclusively in the process memory of the
respective `InstanceResolver` instance (`dict[str, int]`, key = instance
address) — with multiple horizontally scaled gateway replicas behind a load
balancer, each replica only sees the requests it ITSELF is currently
forwarding to a target instance, not the sum across all replicas. This is a
deliberate design decision of this session, **not an oversight** — and an
intentional contrast to the directly adjacent P25-S3 (see "Rate Limiting"
above): there, the counter was deliberately moved to Redis because a purely
local rate-limit counter could be circumvented by a client distributing
requests across multiple replicas (a genuine security problem). For load
balancing, this incentive to circumvent is absent — in the worst case, the
instance selections of multiple replicas are somewhat less optimally
coordinated with each other, which distributes load noticeably unevenly but
without security relevance. A genuine cluster-wide view (e.g. likewise via
Redis, with `INCR`/`DECR` per instance address) would be possible but was
deliberately NOT implemented here: the additional Redis round trip on every
single proxied request (two extra network hops per request, both BEFORE and
AFTER the actual upstream call) is out of proportion to the benefit for a
value that is only a load-balancing heuristic anyway, not a hard access
barrier like rate limiting.

## Auth Validation

JWT check, central for all proxied requests. **Since Phase 18 Session 2**
([ADR 0064](../adr/0064-superuser-migration-lokale-tokens-gateway-multi-issuer.md)):
`app.state.token_validator` is a `MultiIssuerTokenValidator` (new in
`libs/dms-auth-client`) composed of two `TokenValidator` instances — Keycloak
JWKS (as before) and `auth-service`'s `/.well-known/jwks.json` (new
`DMS_AUTH_SERVICE_BASE_URL` setting, direct east-west address) for tokens of
local technical accounts (superuser, later domain admins). Without this
change, a freshly, locally logged-in superuser would fail with `401` on every
proxied call, even though `auth-service` correctly validates its own token —
verified live (`GET /me` and a `document-service` call with a locally issued
token, both via the real gateway, were correctly passed through). With the exception of the routes
listed in `settings.public_routes` (default: `auth-service:login`,
`auth-service:refresh`, since a token is needed first to obtain those; since
P6-S9 additionally the two federation-hub inbound endpoints; since P13-S2
additionally four routes for the independently operated
`fleet-management-service` — `registry-service:installation`,
`license-service:license/status`, `license-service:license`,
`config-service:config/fleet-import` (**until P17-S1**: `config-service:
config/import`, see "Correction" below), see
[ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md);
since the ad-hoc post-roadmap SSO feature additionally `auth-service:oidc/authorize` and
`auth-service:oidc/callback` (the login entry point itself, same rationale as
`login`/`refresh` — no token exists before login has even taken place; both also
in `maintenance_mode_allowed_routes`, see [ADR 0062](../adr/0062-sso-automatischer-login-oidc-redirect-und-optionales-kerberos.md));
since P14-S10 additionally `document-service:public/share-links` and
`document-service:public/share-links/content` for the public
share link (4.2a) — anonymous viewers hold no bearer token for this
installation, the actual access secret instead being the
share-link token itself, which travels as a query parameter (`?token=...`,
not as a path segment) and is checked by `document-service`; as a result,
these two new entries remain simple, static exact-match strings
without wildcard matching logic at the gateway itself, see
[ADR 0047](../adr/0047-public-share-link-query-param-token-and-disable-semantics.md)).
On success, the identity claims are forwarded to the downstream as
`X-DMS-Principal`/`X-DMS-Username`/`X-DMS-Roles` headers — originally
(ADR 0005) consumed by no backend service, but meanwhile the basis of
several real checks (`search-service`/`teamspace-service` since P14-S6/P5-S4,
`document-service` since P14-S10, `permission-service`/`workflow-service`
since P14-S11, among others). For a public `public_routes` route, the
gateway passes an `Authorization` header already present in the original
request through to the downstream unchanged (no overwriting with empty
identity headers) — the basis for the fleet-agent key bypass mentioned
above, which only the respective target service itself checks, not the
gateway.

**Security finding + fix (P14-S11 live verification, [ADR 0049](../adr/0049-gateway-header-spoofing-fix-strip-client-x-dms-headers.md)):**
until this finding, a client with a valid bearer token could send its own
`X-DMS-Principal`/`-Roles` header, which was NOT overwritten by the real,
JWT-derived value — a case of case-sensitivity mismatch between Python dict
keys (`upstream.filter_headers()` retained the ASGI-normalized, lowercase
spelling of the incoming header, while `identity_headers` in the source code
is uppercase — both ended up as two separate headers at the downstream).
`filter_headers()` has since removed every incoming `X-DMS-*` header
regardless of its casing, before the real identity headers are set. Verified
live: the same spoofing attempt has since demonstrably returned the real
identity.

**Correction to `public_routes` (P17-S1 finding, [ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md)):**
`config-service:config/import` shared a single public path between RBAC
callers (real, logged-in `config-admin` users) and the fleet-agent key (ADR 0037)
until P17-S1. For paths in `public_routes`, `proxy()` **fundamentally
validates no bearer token and never sets `X-DMS-Principal`** — regardless of
whether a valid token is present in the request. This meant: the RBAC branch
of `config-service`'s import gate was effectively unreachable for ANY call to
this path via the gateway, even for real admins — only discovered at the
first admin-UI hookup of `config-service` (P17-S1; there was no frontend page
for it before then). Fix: a dedicated, still-public path
`config-service:config/fleet-import` exclusively for the fleet agent;
`config-service:config/import` has since been a regular, token-required path.
Test `test_config_import_route_now_requires_gateway_auth_check` explicitly
confirms that this path has since required a bearer token.

## Emergency Shutdown / Maintenance Mode (4.8, since P6-S6)

At the start of every request (before the `public_routes` check), `proxy()` queries the Permission Service's maintenance-mode status via a new `MaintenanceStateClient` — analogous to the `InstanceResolver` pattern, resolved via the registry rather than a fixed URL, with short caching (`maintenance_cache_ttl_seconds`, default 5s). If maintenance mode is active, every request outside `settings.maintenance_mode_allowed_routes` (login/refresh/me/superuser status, Permission Service maintenance-mode status/lift) is rejected with `503`. If the status query itself fails (Permission Service unreachable), **the client fails open** (last cached value, default `false`) — an unreachable Permission Service should not block all proxied traffic.

Every request passed through (even outside maintenance mode) additionally carries an `X-DMS-Maintenance-Active: true`/`false` header — backend services that need to react to the state themselves (`auth-service`'s `/login`, `workflow-service`'s instance start/task completion) read it directly instead of setting up their own polling connection to the Permission Service (header-broadcast pattern instead of N×polling, see [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md)). **The login call itself must consequently go through the gateway** for the maintenance-mode header to be set at all — a direct call to the Auth Service bypassing the gateway never sees the header and is thus unaffected by maintenance mode (the same structural limitation as with the directly published backend ports, ADR 0005).

## CORS

The browser frontends (`user-ui`, `admin-ui`) run on a different origin than the gateway — `CORSMiddleware` allows `settings.cors_allowed_origins` (default: `http://localhost:3000`/`:3001`, the default ports of both frontends), `allow_credentials=False` (auth runs via the `Authorization` header, not cookies). **Incident & fix**: initially missing entirely — curl-based verification did not reveal this, since curl does not trigger a preflight `OPTIONS` request. In a real browser, this caused login to already fail with `405` on the preflight, before the actual `POST /login` was even sent. When `USER_UI_PORT`/`ADMIN_UI_PORT` are changed, `DMS_CORS_ALLOWED_ORIGINS` (JSON array) must be adjusted accordingly.

## Rate Limiting

Sliding window per client (`sub` claim for authenticated clients, otherwise
client IP), `rate_limit_max_requests`/`rate_limit_window_seconds` (default
**600/60s**, see below). Also applies to public routes (login protection). On
exceedance, `429`.

**Since P25-S3 ([ADR 0097](../adr/0097-gateway-rate-limiting-redis-sliding-window.md))
a shared Redis store instead of an in-process `dict`**: originally
(ADR 0005) a purely local counter with no shared counting across multiple
gateway instances — under horizontal scaling of the gateway (multiple
replicas behind a load balancer), a client could effectively multiply the
limit by distributing its requests across multiple replicas. `RateLimiter`
now stores the counter in Redis (new `redis_url` setting, new
`redis` service in `infra/docker-compose.yml`, default `redis://redis:6379/0`
in the Compose environment) — all gateway instances see the same counter per
client key. `allow()` is accordingly `async` (Redis access is
inherently asynchronous), the sole caller in `proxy()` calls it with `await`.

**Sliding window via sorted set instead of fixed window**: implemented over a
Redis sorted set per client key (`ZADD`/`ZREMRANGEBYSCORE`/`ZCARD` in
a MULTI/EXEC transaction), not via the simpler fixed-window
variant (`INCR`+`EXPIRE`). A fixed window briefly allows up to
`2 × max_requests` through at the window edge (the end of window N and the
start of window N+1 coincide in time for a client) — a real weakness for a
login protection. The sorted-set approach reproduces the original
`deque` semantics almost 1:1; the cost is one sorted-set member per request
instead of a single counter, negligible at the window sizes typical here.
See ADR 0097 for details/trade-off discussion.

Redis is deliberately run without persistence in this stack (`--save ""`,
no AOF, no volume) — the rate-limit data is purely transient (TTL per
client key), a restart of Redis itself harmlessly resets the limit. Verified
live (P25-S3): the limit was lowered to 3 requests/120s, the fourth request
returned `429`; the `gateway-service` container was then restarted (fresh process, new
`RateLimiter` instance) — the immediately following request from the same client
still returned `429`, thereby proving that the counter actually lives in
Redis and does not end up in a new in-process cache.

**Default raised from 120 to 600 following user feedback**: the original default (120
requests/60s) triggered `429` very quickly under entirely normal interactive use — not a
bug in the client, Auth Service, or Keycloak (all three genuinely checked and ruled out),
but simply sized too tight for this SPA. A single page load of the three-column
workspace alone fires a good dozen parallel calls (folders, documents, favorites,
approval configuration, reference-number config, object types, `auth-service:me`/`me/preferences`),
plus `MaintenanceBanner`'s 30-second poll in both frontends — normal, active clicking
trivially exceeds 120 calls per minute for a single logged-in user (own
`sub` key, see above). Confirmed live in the running gateway log: a real burst
showed `429` simultaneously on `document-service`/`folder-service`/`object-type-service`/
`permission-service`/`auth-service` routes. 600/60s gives real usage substantially more
headroom while remaining a genuine safety net against clients that actually run out of control.

## Events

Publishes/consumes no events of its own — a pure synchronous proxy.

## Self-Registration

Does **not** register itself with the registry — it is the
entry point where clients arrive directly via a fixed published port,
not something other services need to look up.

## Sensors (Concept 10.1)

None yet — to follow in Phase 11.

## Open Points

- Identity headers (`X-DMS-Principal`/`X-DMS-Username`/`X-DMS-Roles`) are
  still not consumed by EVERY backend service — many endpoints that need a
  principal still accept it explicitly as a parameter/body field (e.g.
  `permission-service`'s older `/check`, whose `principal_id` comes as a
  query parameter rather than from the header). Real header consumers by now:
  `search-service` (since P5-S4), `teamspace-service`/`auth-service`'s
  `/users/lookup` (since P14-S6), `document-service`'s
  share-link endpoints (since P14-S10), `permission-service`'s
  delegation endpoints/`workflow-service`'s task completion "on behalf of"
  (since P14-S11). **Until P14-S11, this header was incorrectly considered
  fully trustworthy** — a client could send it itself and thereby overwrite
  the real, JWT-derived identity (fixed, see
  [ADR 0049](../adr/0049-gateway-header-spoofing-fix-strip-client-x-dms-headers.md)).
  **The `X-DMS-Maintenance-Active` header (4.8, since P6-S6) is consumed by
  two services** (`auth-service`'s `/login`, `workflow-service`'s
  instance start/task completion) — see "Emergency Shutdown / Maintenance Mode" above.
- Backend service ports remain directly published in the Docker Compose
  environment (developer convenience) — a genuine network perimeter that
  makes backends reachable exclusively via the gateway is a later
  deployment step.
- Since P25-S4, least-open-connections selection is used instead of random
  instance selection (see "Instance Selection / Load Balancing" above), but it remains
  **not latency-aware** — an instance with few open requests, but a high
  response time, is neither detected nor avoided as a result. The
  counter is also purely per gateway replica (not a
  cluster-wide value, deliberately so — see above), unlike the
  rate-limit counter shared via Redis since P25-S3.
- Redis runs without auth/TLS in the bundled dev/test stack (dev-only, the
  same stance as Postgres/NATS/MinIO in this stack) — a real installation
  would need to provide its own credentials/network segmentation for the
  `redis` service, which is not modeled here.
