# 0251 — Central log aggregation and trace ↔ log correlation

**Status:** accepted
**Context:** P94-S2 (Konzept §10.1, central log aggregation), after distributed tracing in P94-S1
([ADR 0250](0250-distributed-tracing.md)).

Every service already wrote structured JSON to stdout (`dms_common.logging`), but there were three gaps:
- **No central store.** Logs lived only in each container's log, gone on recreate. A request crossing six
  services meant six `docker logs` and a guess at the timing.
- **Plain-text access logs.** uvicorn's access log bypassed the JSON formatter; it configures its own
  handlers before importing the app.
- **No link between a log line and its trace.**

## Decision

**Loki as the store, Grafana Alloy as the shipper.** promtail is deprecated in favour of Alloy, and Alloy
covers both Docker and Kubernetes with the same processing stages.

- **Compose:**
  - **Loki 3.2.1:** single binary, local filesystem, TSDB schema v13, 7 days retention. The ingest limit
    is raised to 16 MB/s, because Alloy reads each container's whole log once when it first sees it.
  - **Alloy v1.4.3:** discovers every container of the compose project `dms` through the Docker socket
    (read-only). Its read positions sit on a volume, so a recreated Alloy doesn't ship everything again.
- **Helm** (`logging.enabled`, off by default):
  - **Alloy** is one Deployment that streams the namespace's pod logs through the Kubernetes API
    (`loki.source.kubernetes`). It needs a ServiceAccount and a namespace-scoped Role on `pods` and
    `pods/log`: no DaemonSet, no hostPath, no cluster-wide rights. One replica, since two would ship every
    line twice.
  - **Loki** is either bundled (single binary, PVC) or external via `logging.loki.external.pushUrl`. The
    chart fails to render if that URL is missing.

**Labels stay few:** `service` (compose service or the pod's `app.kubernetes.io/component`), `container`
(or `pod`), and `level` from the JSON line. `trace_id` is **structured metadata**, not a label: one value
per request would explode the index. LogQL filters on it directly (`| trace_id="…"`). Lines that aren't
JSON (postgres, keycloak, nginx) pass unchanged.

**Every line written inside a trace names it.** `JsonFormatter` adds `trace_id`/`span_id` when the current
span is sampled. Unsampled ids are left out, since they lead nowhere in Tempo.

**A structured access log replaces uvicorn's.**
- **Routing:** `configure_logging` routes uvicorn's loggers through the JSON handler, and its access
  logger goes to WARNING.
- **The new line:** `dms_common.access_log.AccessLogMiddleware` writes one line per request (`dms.access`).
  Its fields are `http_method`, `http_route` (the route template, so lines group by endpoint),
  `http_path`, `http_status` and `duration_ms`. It is WARNING from status 500 on; `/healthz` and
  `/metrics` are skipped.
- **The call:** `setup_observability(app, settings)` replaces P94-S1's `setup_tracing` call in all 32
  services. It adds the middleware first, so the tracing middleware wraps it and the access line is
  written inside the request's span. uvicorn's own line comes after the response, outside any trace,
  which is why it was replaced rather than reformatted.

**Grafana, both directions:**
- **Loki datasource:** a derived field turns `"trace_id": "<32 hex>"` in a line into "Trace öffnen"
  (Tempo).
- **Tempo datasource:** `tracesToLogsV2` opens a span's service logs for that trace (`service.name` →
  `service`, ±2 min).
- **Helm:** two datasource ConfigMaps for a Grafana sidecar; Tempo gets the logs link when
  `logging.enabled`.

## Verified live

- **One upload with a known `traceparent`:** Loki returns 32 lines from 11 services for that trace id,
  including the asynchronous processing. Grafana Explore goes from the gateway's access line ("Trace
  öffnen") to the trace (167 spans), and from each span back to its logs.
- **Volume:**
  - The idle dev stack writes about 25 lines/s, most of them access lines.
  - Loki held 38 MB after reading every container's history once.
  - During a regression run the access log grows with the request rate: object-type-service wrote 21,000
    structure checks in 10 minutes.
- **Found through it:** search-service's `document.created` handler gets `403` from document-service for
  documents in teamspace folders (no inherited "everyone" grant, and search-service isn't a member). It
  raises, the message isn't acknowledged, and JetStream redelivers it every 30 s indefinitely: 16
  messages, 64 errors in 10 minutes. Those documents are presumably never indexed. The root cause is a
  question of how a background indexer is authorised, so it isn't fixed here; it is recorded in P94-S3.
  **Resolved in P94-S3** (user decision): document-service trusts a service principal reading as itself
  (no `X-DMS-On-Behalf-Of`) - reads only, never writes, and a service acting for a user is checked as
  before. search-service additionally acknowledges a `403` instead of letting it be redelivered forever.
  Live afterwards: 0 errors, 0 redelivered messages.

## Consequences

- **dms-common:**
  - `trace_id`/`span_id` in `JsonFormatter`, and uvicorn's loggers routed through it;
  - `access_log.AccessLogMiddleware` and `otel.setup_observability`;
  - all 32 services call `setup_observability`.
- **Compose:** `loki`, `alloy` (with the Docker socket, read-only), the volumes `loki-data` and
  `alloy-data`, and the Grafana datasource "Loki". The Tempo datasource gains the logs link.
- **Helm:** `logging.*` (`templates/logging.yaml`) and the Tempo datasource's logs link.
- **uvicorn's plain access lines are gone.** Anyone grepping `docker logs` for `"GET /… HTTP/1.1"` now
  finds JSON with `"logger": "dms.access"`.
- **Not covered:**
  - the Next.js apps' nginx logs are shipped but stay plain text, and the browser isn't covered;
  - log-based alerting (Loki ruler) isn't set up, since alerting stays with monitoring-service's sensor
    rules (ADR 0245).
