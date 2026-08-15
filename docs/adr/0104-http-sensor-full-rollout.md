# 0104 — Generic HTTP request sensors, full rollout beyond the two pilots

**Status:** accepted
**Context:** Post-roadmap, ad-hoc session (user request: "monitoring sensors are still thin, I want per-service request count, avg/p95/p99 response time, error count, and successful requests")

## Decision

Concept 10.1's sensor pilot (P11-S0/S1) deliberately covered only `registry-service` and
`document-service`, each with a couple of bespoke, business-specific sensors
(`document.upload.duration`, `registry.instances.active_total`, ...). This ADR extends the sensor
concept in two ways:

1. **A generic, per-service HTTP request sensor pair**, not another bespoke metric: `http.requests`
   (`Counter`, labeled `method`/`route`/`status`) and `http.request.duration_seconds` (`Histogram`,
   labeled `method`/`route`). Together these cover all five metrics the user asked for via PromQL —
   request count (`sum(rate(http_requests_total[...]))`), average response time
   (`rate(..._sum[...]) / rate(..._count[...])`), p95/p99 (`histogram_quantile(...)`), error count
   (`status=~"5.."`), and successful-request count (`status=~"2.."`) — plus client-error visibility
   (`4..`) as a bonus not explicitly requested but essentially free with the same data. A single
   labeled pair was chosen over six separate `SensorSpec`s: p50/avg/p95/p99 are all *derived* from the
   same histogram, so toggling them independently would be meaningless — the collection cost is
   incurred once per request regardless of how many statistics are read from it afterward.
2. **Full rollout** to every service that already self-registers with `registry-service`, not another
   scoped pilot — the user's own words ("je Service", "per service") and the fact that a second pilot
   round would just repeat the P11-S0 finding rather than resolve it.

### Route, not raw path, as the label

`request.scope["route"].path` (the matched route *template*, e.g. `/documents/{document_id}`) is used
instead of `request.url.path` (the raw path, e.g. `/documents/3db531c3-...`). The raw path would give
every individual document/user/folder ID its own time series — the same cardinality-explosion mistake
already found and fixed once this session, in the k6 load-test script's Prometheus tagging
(`withName()`, see `loadtest/README.md`). Unmatched routes (404s from probes/typos) fall back to a
fixed `"unmatched"` label for the same reason, rather than the raw (attacker-controlled) path.

### Must run at module level, not inside `lifespan`

`bootstrap_http_sensors(app, service_name)` calls `app.middleware("http")` internally to install the
instrumentation. FastAPI raises `RuntimeError: Cannot add middleware after an application has started`
if this is attempted from inside the async `lifespan` function — Starlette freezes the middleware stack
once the ASGI lifespan protocol begins. Every existing sensor-service's `main.py` previously built its
entire `SensorRegistry` inside `lifespan`; this had to change: `bootstrap_http_sensors` is called once,
at module level, immediately after `app = FastAPI(...)`.

### `SensorConfigProxy`: decoupling the module-level registry from the per-lifespan config client

A `SensorConfigClient` owns an `httpx.AsyncClient`, whose connection pool binds to whichever asyncio
event loop first uses it and breaks (`RuntimeError: Cannot send a request, as the client has been
closed`) once that loop is gone. This is invisible in production (one process, one event loop, one
lifetime) but fatal for tests: each `TestClient(app)` `with` block runs its own fresh event loop against
the same, module-cached `app` — a naive "build `SensorConfigClient` at module level too" approach
(tried first, reverted) broke every test after the first one in a session.

Fix: `bootstrap_http_sensors` returns a `SensorConfigProxy` (module-level, holds no network resources)
instead of a `SensorConfigClient`. The `SensorRegistry`'s sensors bind to `proxy.is_active` once, at
import time. `lifespan` constructs a **fresh** `SensorConfigClient` on every startup, calls `.start()`,
and `proxy.bind(client)`s it — teardown `proxy.unbind()`s and `.stop()`s it. Fail-open (`True`) while
unbound, matching `SensorConfigClient`'s own pre-first-poll fail-open behavior.

### `service` label added at the monitoring-service merge step

Prometheus scrapes only `monitoring-service` (pull/proxy model, ADR predates this one — see
`docs/operations/monitoring.md`), which merges every scraped instance's `/metrics` into one response
and already tagged each sample with an `instance` label (opaque instance ID) for disambiguation. That
alone isn't useful for the new per-service dashboards, which need to group/filter by *service type*, not
instance UUID — `monitoring_service.scraper.merge_metric_families` now also injects a `service` label
from `RegistryInstance.service_type` (already available, no new registry-service endpoint needed).

### Excluded from the rollout

- **`federation-hub-service`, `fleet-management-service`** — both are explicitly **not** internal
  services of a single installation (ADR 0028; see their own `Settings` docstrings) and don't
  self-register with `registry-service` at all. Instrumenting them via the same
  installation-internal monitoring-service proxy would cross that deliberate boundary.
- **`monitoring-service` itself** — already self-registers; adding it to its own scrape target list
  risks a self-referential scrape. Low value (low-volume admin-API traffic) for the added complexity
  of guarding against that.
- **`gateway-service`** is the one exception added *to* scope, not excluded: it did not previously
  self-register at all (nothing needs to discover the fixed entry point via service discovery), but
  gateway-level metrics (all API traffic funnels through it) are arguably the most valuable of any
  single service to monitor - self-registration was added purely so monitoring-service can find and
  scrape its new `/metrics`, with no change to its actual request-proxying behavior.

## Consequences

- New Grafana dashboard `infra/grafana/dashboards/dms-service-http-overview.json` — a `$service`
  dropdown (from `label_values(http_requests_total, service)`) drives per-service detail panels, plus
  an all-services overview table.
- Test files that asserted an *absolute* Prometheus counter value from `/metrics` (there was exactly
  one, in `document-service`) needed a before/after-delta rewrite: sensor state now persists for a
  process's whole lifetime (correct, matches production) instead of being incidentally rebuilt fresh on
  every `TestClient(app)` cycle in tests (an accidental side effect of the old per-lifespan
  construction, never a deliberate design goal).
- `libs/dms-metrics-client`'s `SensorRegistry.counter()`/`.histogram()` gained an optional `labelnames`
  parameter, and `GuardedCounter.inc()`/`GuardedHistogram.observe()` gained `**labels` - both
  backward-compatible (existing unlabeled bespoke sensors are unaffected).
