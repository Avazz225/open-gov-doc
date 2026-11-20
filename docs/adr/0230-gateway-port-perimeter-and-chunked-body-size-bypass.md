# 0230 — Bind backend service ports to loopback; close the chunked-encoding body-size bypass

**Status:** accepted
**Context:** P78-S2 (Phase 78, second session of the tenth gap-analysis round, run after Phase 77
closed). Two independent request/network-boundary hardening gaps, found in the same sweep, bundled into
one session per the plan's own grouping (not because they share a fix, but because both are
"the request/network boundary doesn't yet enforce something it should"):

1. **Gateway backend-port bypass** (ADR 0005's own "Consequences," never revisited since): every one of
   the 31 domain backend services publishes its port directly to the host in `infra/docker-compose.yml`
   ("developer convenience," ADR 0005's own words) — a caller on the same network as the Docker host can
   reach a backend service directly, at its real published port, completely bypassing the gateway's
   centralized auth validation, rate limiting, and maintenance-mode enforcement.
2. **Chunked-encoding body-size bypass** (ADR 0187's own "Consequences"): `libs/dms-common`'s shared
   `MaxBodySizeMiddleware` (`virus-scan-service`/`rendering-service`/`storage-service`) only inspected the
   declared `Content-Length` header — a genuinely chunked-transfer-encoded request, which never sends
   `Content-Length` at all, skipped the size check entirely and could grow unbounded in memory.

## Decision

**1. Bind all 31 domain backend services' `ports:` mappings to `127.0.0.1` explicitly**, instead of the
implicit `0.0.0.0` every `"${VAR:-default}:8000"` shorthand resolves to. `gateway-service` itself and the
six frontend apps (`user-ui`/`admin-ui`/`reviewer-ui`/`process-designer`/`migration-console`/
`office-addin`) deliberately keep their existing `0.0.0.0` binding — a browser on another machine on the
same network is a legitimate use case for those two categories, unlike a direct call to e.g.
`permission-service`. Infrastructure containers (Postgres/Keycloak/NATS/MinIO/Azurite/mailpit/Redis/
Prometheus/cAdvisor/Grafana) are out of this session's scope — a separate category with their own,
already-documented dev-only security posture, not "backend services bypassing the gateway."

**2. Rewrite `MaxBodySizeMiddleware` as a raw ASGI middleware** (was `BaseHTTPMiddleware`) that wraps
`receive` directly. The `Content-Length` fast path is unchanged (rejects immediately from the header
alone, no buffering — covers the overwhelming majority of real upload clients). For the remaining case
(no `Content-Length`, or one that lies), it now buffers incoming body chunks itself, byte-counted as they
arrive, up to `max_bytes + 1`; if that bound is reached before the body ends, it rejects with `413`
immediately and never invokes the wrapped application. A request within the limit is fully buffered by
the middleware, then replayed through a `receive` substitute so the downstream app sees byte-identical
messages to what it always did.

## Rationale

- **Why `127.0.0.1`, not removing host publishing entirely**: this project's own established workflow —
  used constantly throughout this entire session's history and every prior one — is direct
  `curl http://localhost:<port>/...` against individual backend services for testing, role-assignment
  management, and verification. Removing host publishing entirely would close the gap but break this
  workflow completely. Binding to `127.0.0.1` closes the *actual* exploitable shape the finding describes
  (ADR 0005's own words: "a caller on the same network") while leaving `localhost`/`127.0.0.1` access on
  the host itself, and all inter-container traffic (which never goes through host port mappings at all,
  regardless of binding), completely untouched. Verified directly: `curl localhost:8004/healthz` and
  `curl 127.0.0.1:8004/healthz` both still return `200`; `curl <host-LAN-IP>:8004/healthz` now fails to
  connect at all (previously `200`); a real end-to-end login through the gateway (which internally calls
  `auth-service` container-to-container) still succeeds, confirming zero impact on legitimate traffic.
- **Why Kubernetes/Helm needed no change**: `infra/k8s/dms/values.yaml`'s chart never defines a
  `NodePort`/`LoadBalancer` Service anywhere — every Service defaults to `ClusterIP` (cluster-internal
  only, no node/LAN reachability at all), a stricter posture than even the fixed Docker Compose stack.
  This was a Docker Compose-only gap from the start.
- **Why a raw ASGI middleware instead of extending the existing `BaseHTTPMiddleware`**: `BaseHTTPMiddleware`
  hands the endpoint a `Request` whose body it would need to fully consume itself to count bytes — at
  which point Starlette can no longer serve that body to the real endpoint downstream (it only caches
  `request.body()` if read via that exact method, not via manually iterating `request.stream()`). A raw
  ASGI middleware wrapping `receive` counts every byte exactly once as it actually arrives, with no
  double-consumption risk, and can synthesize its own `413` response via `send` directly with no
  dependency on FastAPI's exception-handling layer (no per-service `add_exception_handler` wiring needed
  in any of the 3 consuming services).
- **Why buffer-then-replay instead of streaming-abort-mid-flight**: cleanly aborting a request that a
  downstream ASGI/Starlette parser (e.g. a multipart form parser) is already mid-way through consuming,
  without corrupting its internal state or producing a confusing 500, is a genuinely hard problem with no
  safe, well-established pattern in this codebase's dependency set. Buffering up to `max_bytes + 1` bounds
  memory usage to the exact same order of magnitude a legitimate request under the limit already costs the
  downstream app to hold anyway — a request actually within the limit never sees a behavior change; the
  cost is paid only by the case that gets rejected either way.

## Consequences

- `infra/docker-compose.yml`: 31 `ports:` entries changed from `"${VAR:-default}:8000"` to
  `"127.0.0.1:${VAR:-default}:8000"` — `registry-service` through `teamspace-service`, `object-type-
  service`, `folder-service` (every domain backend service except `gateway-service` itself).
  `docker compose config` validated clean.
- `libs/dms-common/src/dms_common/upload_limits.py`: `MaxBodySizeMiddleware` rewritten from
  `BaseHTTPMiddleware` to a plain ASGI `__call__(self, scope, receive, send)` class. Public interface
  (`MaxBodySizeMiddleware(app, max_bytes=...)`, `app.add_middleware(MaxBodySizeMiddleware, max_bytes=...)`)
  unchanged — zero code change needed in any of the 3 consuming services
  (`virus-scan-service`/`rendering-service`/`storage-service`).
- `docs/services/gateway-service.md` and `docs/services/migration-service.md`-style closure note added
  for the port-perimeter gap (struck-through, closed); `libs/dms-common`'s own module docstring extended
  in place (no separate `docs/services/*.md` page for a shared lib).
- New/updated tests: `libs/dms-common` — `test_request_without_content_length_header_is_not_blocked`
  (previously asserted the old, now-closed gap's behavior) replaced with
  `test_chunked_request_over_limit_without_content_length_is_rejected_with_413` (asserts `413`) and
  `test_chunked_request_under_limit_without_content_length_still_reaches_the_app` (asserts the
  buffer-then-replay path delivers byte-identical data to the endpoint for a request within the limit),
  5/5 total. `virus-scan-service`/`rendering-service`/`storage-service` full suites re-run unchanged
  (310 passed combined) — confirms the middleware rewrite doesn't break any real multipart upload
  endpoint in the three consuming services.
- All 31 rebound services + the 3 upload-limited services rebuilt/redeployed; live-verified against the
  real running stack as described in "Rationale" above (loopback access preserved, LAN access blocked,
  gateway still LAN-reachable, real end-to-end login through the gateway still succeeds).

## Follow-up (P84-S3): infrastructure containers bound to loopback too

The infrastructure ports this ADR left "out of scope" are now bound to loopback as well, through two
opt-in variables (both default `127.0.0.1`, documented in `infra/.env.example` and at the top of
`infra/docker-compose.yml`):
- **`INFRA_BIND_ADDRESS`:** Postgres, NATS client, MinIO API, Azurite, Mailpit SMTP/POP3, Redis. These
  are never meant for the LAN.
- **`INFRA_UI_BIND_ADDRESS`:** Keycloak, NATS monitor, MinIO console, Mailpit UI, Prometheus, cAdvisor,
  Grafana. These are browser UIs, so setting this to `0.0.0.0` lets an operator look at e.g. Grafana
  from another device without opening the databases.

Nothing in the dev workflow needed LAN access to them:
- the test runner uses `localhost:5432`/`:4222`;
- Keycloak's public base URL is already `http://localhost:8080`, so a browser SSO redirect never
  worked from another device;
- every service reaches the infrastructure over the compose network.

Live-checked:
- all 14 ports listen on `127.0.0.1` only;
- loopback connects, and the host's LAN address is refused;
- gateway and frontends are still reachable from the LAN;
- backend tests via localhost and the user-ui/admin-ui e2e suites pass;
- `INFRA_UI_BIND_ADDRESS=0.0.0.0` renders Grafana on `0.0.0.0`.
