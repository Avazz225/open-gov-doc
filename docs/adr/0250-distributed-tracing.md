# 0250 — Distributed tracing

**Status:** accepted
**Context:** P94-S1 (Konzept §10.1 "distributed tracing complements the sensor metrics"); the open question
of [ADR 0254](0254-permission-cache-on-registration-and-db-connection-budget.md) ("What remains: about 2×
per-request latency at low load").

`dms_common.otel.configure_tracing()` had existed since the first sessions, but no service called it. There
was no exporter, no collector and no trace store. A request crosses three to six services, and nothing
showed where its time goes.

## Decision

**One call per service.** `setup_tracing(app, settings)` runs right after `app = FastAPI(...)` in all 32
services. Without `DMS_OTEL_EXPORTER_ENDPOINT` it does nothing at all. With the endpoint set:
- **Exporter:** OTLP/HTTP in batches to the collector.
- **Incoming requests:** FastAPI instrumentation. Health and scrape endpoints are excluded, and so are the
  per-chunk ASGI "send/receive" spans, which tripled the span count and add nothing to a breakdown.
- **Outgoing calls:** httpx instrumentation. The W3C `traceparent` header continues the trace in the next
  service; the gateway's proxy calls are covered the same way.
- **Database statements:** SQLAlchemy instrumentation, async engines included. It only applies where
  SQLAlchemy is installed; gateway-, config-service and the two connectors have no database.
- **Events:** `dms_eventbus_client` writes `traceparent` into the NATS message headers on publish. It
  continues the trace in a CONSUMER span around the handler, so a document's processing chain (OCR,
  rendering, search) hangs under the upload that caused it. It uses only the OpenTelemetry API, so it is a
  no-op without a provider.

**Sampling: parent-based, decided once, adjustable at runtime.**
- **One decision per request.** The first service of a request decides, and every later hop follows the
  sampled flag in `traceparent`. A request is traced across every hop or not at all.
- **The ratio lives in monitoring-service** (table `tracing_config`, `GET/PUT /tracing-config`,
  `admin.monitoring` to change it). Changes publish `monitoring.tracing.config_changed`, which the audit
  log records.
- **Distribution without per-service wiring:** monitoring-service hands the ratio out with
  `GET /sensor-config`, which 30 services already poll every 15 s. `setup_tracing` registers the
  process's sampler with `dms_metrics_client`'s config client. monitoring-service sets its own sampler
  directly. fleet-management-service keeps its `DMS_OTEL_SAMPLING_RATIO`, because it runs outside the
  installation's monitoring.
- **Only entry points start traces:** an incoming request, a consumed event, or an event a background job
  publishes. Without this rule every poll loop's database query and every sensor-config poll would be its
  own one-span trace; the first live run was full of `connect` traces from mail-connector's loop. Inside
  a request those spans appear as children as usual.

**Infrastructure:**
- **Compose:** `otel-collector` (contrib 0.111.0, batch and memory limiter) → `tempo` (2.6.1, single
  binary, local storage, 72 h retention), plus a provisioned Grafana datasource "Tempo" (uid `tempo`). All
  32 services get `DMS_OTEL_EXPORTER_ENDPOINT=http://otel-collector:4318`; setting it empty in `.env`
  switches tracing off.
- **Helm:** `tracing.enabled` (default off) with `samplingRatio` (default 0.1). The bundled variant is a
  collector and a Tempo with a PVC. `bundled.enabled: false` uses `external.endpoint` instead, and the
  chart fails to render if that endpoint is missing. A ConfigMap labelled `grafana_datasource: "1"`
  provides the datasource for a Grafana sidecar. Grafana itself stays outside the chart, as before.

**admin-ui:** the monitoring sensors page has a "Tracing" section: the recorded share in per cent, who
changed it and when, and a note when monitoring-service has no collector configured.

## Measured: why requests are 2× slower than in August

Each action was run 60 times sequentially through the gateway, each with its own sampled `traceparent`,
after a warm-up (`users-admin`, a fresh folder). Every trace was fetched from Tempo and cut at CONSUMER
spans, so only the synchronous part before the response counts.

For each service the breakdown records:
- its **own time**: the server span minus its outgoing calls;
- its **DB time**;
- each **outgoing call**.

The client medians are the same without tracing (ratio 0, no `traceparent`): 13.3 / 21.0 / 53.3 ms against
13.8 / 21.3 / 53.4 ms. Tracing costs under 0.5 ms per request.

| Action (client median) | Hops and medians |
|---|---|
| `list_folder` (13.8 ms) | gateway own 2.7 · folder-service own 3.0, DB 0.35 · **folder → permission `/check` 4.7** (permission-service own 3.1, DB 0.4) |
| `download` (21.3 ms) | gateway own 2.7 · document-service own 5.4, DB 0.4 · **document → permission `/check` 5.2** (own 3.7) · document → storage 3.5 (own 2.3) · NATS publish 0.3 |
| `upload` (53.4 ms) | gateway own 3.0 · document-service own 9.9 · **document → folder-service 11.0** (folder own 3.7, its own permission check 4.9) · document → virus-scan 8.5 (**virus-scan's own permission check 3.4**) · document → storage 6.2 · **document → permission `/resources` 4.7** · **document → permission `/check` 4.3** |

**What grew since August** (the code of 2026-08-15, commit `452d3a9`):
- **Hops that didn't exist.**
  - In August the children listing and the download made no permission call at all. Today each makes
    one, and that one call is the whole difference: listing +4.7 ms (5 → 11), download +5.2 ms (10 → 17).
  - In August the upload called virus-scan and storage. Today it adds:
    - the target folder lookup in folder-service, with that service's own permission check;
    - the write check;
    - registering the document's resource node;
    - virus-scan's permission check.

    That is about 25 ms of synchronous calls, matching 25 → 42 ms.
- **The cost is per hop, not per statement.**
  - The database is cheap: 0.3–0.4 ms per statement, one to four statements per request.
  - Every service hop costs 2.5–4 ms of the service's own time even when the work is a cache hit. A
    permission check is about 1.5 ms of wire plus 3–3.7 ms in permission-service, of which 0.4 ms is the
    database.
  - That fixed cost is Python request handling: the middleware stack (HTTP sensors, acting identity,
    logging) plus serialisation. Tracing doesn't explain it (see above).
- **Not the cause:** the permission cache (one statement per check, as intended since ADR 0254), the
  gateway (2.7 ms own time, its maintenance-mode lookup cached), and the event fan-out. A download sets
  off 7 event handlers and an upload 19, but they run after the response.

**What follows from it** (not done here, recorded for P95):
- **Fewer synchronous hops, not faster ones.** Options:
  - permission decisions inside the calling service from a short-lived cached grant (the check per
    request is the largest single item in listing and download);
  - the upload's folder lookup and write check answered in one call;
  - resource-node registration after the response;
  - virus-scan trusting the already-checked upload call.
- **The fixed cost per hop** deserves a profile of one service's middleware stack.

## Consequences

- **dms-common:** `setup_tracing`, `AdjustableRatioSampler` (entry points only), `set_sampling_ratio`. New
  dependencies are the OTLP exporter and the FastAPI, httpx and SQLAlchemy instrumentation.
  `BaseServiceSettings.otel_sampling_ratio`.
- **dms-eventbus-client:** trace context in NATS headers; depends on `opentelemetry-api`.
- **dms-metrics-client:** `register_tracing_ratio_listener`; `tracing_sampling_ratio` read from
  `GET /sensor-config`.
- **monitoring-service:** `tracing_config`, `GET/PUT /tracing-config`, the ratio in `GET /sensor-config`.
- **All 32 services:** the `setup_tracing` call. Compose sets the endpoint, Helm sets it with
  `tracing.enabled`.
- **Volume:** at ratio 1 one upload is about 60 spans including its event handlers. The dev stack keeps
  1.0; Helm starts at 0.1.
- **Not traced:** the browser and the Next.js apps (traces begin at the gateway), aiohttp calls
  (signature-service's certificate fetching), and the LibreOffice and Office add-ins.
- **Logs:** log ↔ trace correlation follows in P94-S2.
