# reporting-service

**Responsibility:** Reporting Service (5.4) with two functions: **a) Standard reports** (5.4a, since P7-S2b) — predefined, exportable analytics over system state that can be scheduled for email delivery: document volume, open workflow tasks, storage consumption per backend, user activity. **b) Forensic trace** (5.4b, since P7-S2c) — object-related tracking ("all actions by user X"/"all users on document Y"), central use case: compromised account.

**Concept reference:** 5.4/5.4a/5.4b
**Own Postgres schema:** `reporting` (tables `document_created_event`, `report_schedule`, `report_run`)

## Architecture Decisions

- **Only 4 of the 5 concept example reports** — license utilization deliberately excluded, since there is no License Service yet (concept 3.2b/Phase 9, `P9-S0` still pending). 5.4 explicitly lists the reports as examples ("e.g. ..."), not an exhaustive list. Can be retrofitted once Phase 9 delivers the License Service.
- **Mixed data source per report** (concept 3.1 explicitly allows both: "via events/read models **or** a dedicated reporting service with its own replicated view"):
  - **Document volume** = real read model (`document_created_event`, fed by consuming `document.created`) — the only report where a dedicated time-series aggregation actually creates new value (nothing else retains this history).
  - **Open workflow tasks**/**user activity** = synchronous live queries at report generation time against `workflow-service` (`GET /instances`+`.../tasks`) resp. `audit-service` (`GET /events?actor=&since=&until=` — the filter API built in P7-S2, its first real consumer). No event marks a task as "newly due" (only start/completion are published) — a read model would inherently become stale here.
  - **Storage consumption per backend** = synchronous live query against `storage-service`'s new `GET /storage/usage` endpoint (since P7-S2b, see `docs/services/storage-service.md`).
- **`document.created` payload extension**: since P7-S2b, `document-service` additionally sends `folder_id` in the event payload — without this field, the document-volume read model could not group by folder without synchronously querying the Document Service on every event (the live join that 3.1 wants to avoid).
- **Export without a new library**: CSV (Python standard library) + PDF (`reportlab`, already a real dependency in `rendering-service`, reused here).
- **Scheduled delivery without an email attachment**: `notification-service`'s `NotificationCreate` only knows `channel/recipient/subject/body`, no attachment field — retrofitting that would be a separate change to a foreign service. Instead, a `_report_schedule_poll_loop` (the exact same poll idiom as `document-service`'s `_retention_poll_loop`/`workflow-service`'s `_sla_poll_loop`) generates the report when due, uploads it via `storage-service` under `reports/{schedule_id}/{run_id}.{format}` (a `ReportRun` row), and calls `notification-service` with a plain-text email containing a download link to `GET /report-runs/{id}/download` (proxy access, same pattern as document downloads). Ad-hoc exports (`.../export`) are **not** persisted — only scheduled/delivered runs need a storage location for the email link to point to.
- **`gateway_base_url` separate from `self_address`**: `self_address` (registry self-registration) is an internal Docker DNS name, unreachable from outside the Docker network (e.g. a real email client). The download link in the scheduling email therefore uses its own, externally reachable `Settings.gateway_base_url`.
- **Forensic trace (5.4b) as a second function of the same service, not a third new service**: concept 5.4 explicitly describes standard reports and forensic trace as two use cases of the *same* Reporting Service. The already-existing `AuditClient` (from the user-activity report) and the CSV/PDF export functions were directly reused, see below.
- **Categorization instead of maintaining a new taxonomy**: the action-type categories (`view`/`download`/`change`/`delete`) are derived purely from the `event_type` suffix (`forensic.categorize_event_type`) — no new database field, no separate mapping table to maintain.
- **Self-audit via the regular event mechanism instead of a separate log**: every `GET /forensic-trace` query unconditionally publishes `reporting.forensic_trace.queried` (a new, dedicated producer stream `reporting` — until P7-S2c this service only had a consumer bus). This lands directly in the hash-chained, tamper-evident chain of `audit-service` and is itself discoverable again via the regular filter API, instead of creating a parallel, less trustworthy log source.
- **Anomaly hints kept minimal**: exactly one rule type ("more than N downloads by the same actor within M minutes", a sliding window over the hits contained in the trace result) instead of a generic rule engine — the concept explicitly calls this optional and "initially exclusively via configurable static thresholds"; AI/ML anomaly detection is deliberately out of scope.
- **Trace object type "role" not implemented**: `permission-service` currently publishes no events for role/role-assignment CRUD (only approval/scope-lock/maintenance-mode, all with `subject=None`) — role tracking would require its own retrofit there. The concept text mentions role only as an example ("e.g. a role"), not an exhaustive list.

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/reports/document-volume?since=&until=&folder_id=&group_by=day\|week\|month` | Document volume from the own read model, grouped by period (+ optional folder) — `reporting.read`-gated since **P19-S7** |
| `GET` | `/reports/document-volume/export?format=csv\|pdf&...` | Same filters, CSV/PDF download — `reporting.read`-gated since **P19-S7** |
| `GET` | `/reports/open-workflow-tasks` | Live query of open workflow tasks against `workflow-service` — `reporting.read`-gated since **P19-S7** |
| `GET` | `/reports/open-workflow-tasks/export?format=csv\|pdf` | CSV/PDF download — `reporting.read`-gated since **P19-S7** |
| `GET` | `/reports/storage-usage` | Live query `GET /storage/usage` against `storage-service` — `reporting.read`-gated since **P19-S7** |
| `GET` | `/reports/storage-usage/export?format=csv\|pdf` | CSV/PDF download — `reporting.read`-gated since **P19-S7** |
| `GET` | `/reports/user-activity?actor=&since=&until=` | Live query against `audit-service` (`GET /events` filter API), aggregated client-side by `(actor, event_type)` — `reporting.read`-gated since **P19-S7** |
| `GET` | `/reports/user-activity/export?format=csv\|pdf&...` | CSV/PDF download — `reporting.read`-gated since **P19-S7** |
| `POST` | `/report-schedules` | Create a schedule (`report_type`, `format`, `frequency: "daily"\|"weekly"\|"monthly"`, `recipient_email`, optional `filters`) — `reporting.write`-gated since **P19-S7** |
| `GET` | `/report-schedules` | All schedules — `reporting.read`-gated since **P19-S7** |
| `DELETE` | `/report-schedules/{id}` | Remove a schedule — `reporting.write`-gated since **P19-S7** |
| `GET` | `/report-runs/{id}/download` | Proxy download of a generated report run (from `storage-service`) — target of the download link in the scheduling email; `reporting.read`-gated since **P19-S7** |
| `GET` | `/forensic-trace?actor=&subject=&event_type=&category=&since=&until=&limit=` | Forensic trace (5.4b, since P7-S2c) — calls `audit-service`'s P7-S2 filter API, categorizes client-side, computes anomaly hints. `category` ∈ `view`\|`download`\|`change`\|`delete`. Since **P19-S7** ([ADR 0072](../adr/0072-archival-reporting-rbac.md)) `reporting.forensic_trace`-gated (its own, narrower permission instead of `reporting.read`) — the previous, unverified `queried_by` query parameter has been removed; the self-audit actor source is now exclusively the verified `X-DMS-Principal` header |
| `GET` | `/forensic-trace/export?format=csv\|pdf&...` | Same filters, CSV/PDF download — `reporting.forensic_trace`-gated since **P19-S7**, `queried_by` likewise removed |
| `GET` | `/healthz` | Health check |

## Data Model

- `document_created_event`: `id`, `document_id`, `folder_id` (nullable), `occurred_at` — insert-only read model, one row per consumed `document.created`.
- `report_schedule`: `id`, `report_type`, `format`, `frequency` (`daily`/`weekly`/`monthly`), `recipient_email`, `filters` (JSON), `next_run_at`, `last_run_at`, `created_at`.
- `report_run`: `id`, `schedule_id` (FK), `report_type`, `format`, `storage_object_key`, `content_type`, `generated_at` — one row per actually delivered scheduled run (not for ad-hoc exports).

`advance_next_run` advances `next_run_at` after each run: `daily`/`weekly` via a simple `timedelta`, `monthly` via `calendar.monthrange` (correctly handles day overflow, e.g. Jan 31 → Feb 28/29).

## Poll Loop (Scheduled Delivery)

`_report_schedule_poll_loop` (lifespan background task, `Settings.report_poll_interval_seconds`, default 3600s) periodically checks `list_due_schedules` (`next_run_at <= now`). For each due schedule: generate the report (`_generate_report`, same render path as the export endpoints), upload it under `reports/{schedule_id}/{run_id}.{format}` to `storage-service`, create a `ReportRun` row, advance `next_run_at`, send an email with a download link via `notification-service`. The actual tick logic is extracted as a standalone, directly callable `_run_due_schedules(session_factory)` — testable without running the infinite loop itself.

## Forensic Trace (5.4b, since P7-S2c)

Second function of this service (see above) — object-related tracking via the audit filter API built in P7-S2, no own read model needed (`audit-service` remains the authoritative source).

- **`_fetch_forensic_trace`** calls `AuditClient.list_events(actor=, subject=, event_type=, since=, until=, limit=)`, categorizes each hit client-side via `forensic.categorize_event_type(event_type)` (suffix-based: `.downloaded` → `download`, `.viewed` → `view`, `.deleted`/`.force_deleted`/`.trash_purged` → `delete`, everything else → the residual category `change`) and optionally additionally filters by the requested `category`.
- **Anomaly hints** (`forensic.detect_download_anomalies`): sliding window (two-pointer) per actor over their `download` timestamps in the current hit list — reports when more than `Settings.anomaly_download_threshold_count` (default 20) downloads occur within `Settings.anomaly_download_threshold_minutes` (default 5) minutes. Only this one rule type, computed exclusively from the trace results themselves (no additional query).
- **Self-audit** (`_record_trace_query`, a literal concept requirement — "itself audited again as an access"): every `GET /forensic-trace` query (including export) unconditionally publishes `reporting.forensic_trace.queried` with the filters used as payload and `actor=<X-DMS-Principal>` — cannot be disabled, since this is itself the control mechanism. Since **P19-S7**, the header is the sole actor source (previously a separate, unverified `queried_by` parameter, see [ADR 0072](../adr/0072-archival-reporting-rbac.md)).
- **Own producer bus new since this session**: until P7-S2c this service only had a consumer bus (`document.>`). The lifespan gained a second, independent `event_bus` (stream `reporting`, `ensure_stream=True`) — the identical dual-bus pattern as `document-service` (a separate `event_bus` for publishing, a separate `consumer_bus` for subscribing).

## Events

**Consumed** (`document.>`, only `document.created` is relevant, all other `document.*` events are ignored — the same dispatch pattern as `rendering-service`): writes a row to `document_created_event`.

**Published** (stream `reporting`, since P7-S2c — no own producer stream before that):

| event_type | payload |
|---|---|
| `reporting.forensic_trace.queried` | `{actor, subject, event_type, category, since, until}` — the filters used in the query (5.4b, self-audit, see above) |

**NATS JetStream backfill**: the new `document.>` subscription retroactively delivered the entire historical `document.created` event history on first start (the same phenomenon as with `folder.>` in P7-S2) — the read model was thereby immediately populated with real historical data, not only from the rollout point onward.

**Audit connection**: since P7-S2b, Audit Service additionally consumes `reporting.>` — this had no effect until P7-S2c, since this service did not yet publish any events of its own; since P7-S2c it is the first actual producer on this stream (`reporting.forensic_trace.queried`).

## Self-Registration (Concept 3.2a)

Registers itself with the registry on startup (`libs/dms-registry-client`), identical pattern to every other service. Opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Tests

- `uv run pytest services/reporting-service/tests`: repository (aggregation by day/week/month, folder filter, `advance_next_run` incl. month overflow/year change, schedule CRUD, `list_due_schedules`), consumer (`document.created` creates a row incl./excl. `folder_id`, other `document.*` events ignored), reports (all four aggregation functions against fake clients, `to_csv`/`to_pdf` incl. PDF magic-bytes check), API (all endpoints incl. export content type, schedule CRUD, download proxy, poll tick incl. email delivery via a dedicated `poll_env` fixture that deliberately bypasses `TestClient`/lifespan — direct `await` DB access on `app.state.session_factory` from a pytest-asyncio coroutine otherwise fails with `RuntimeError: ... attached to a different loop`, since `TestClient` uses its own event loop), since **P7-S2c** additionally `forensic.py` (categorization of all known event-type suffixes, anomaly detection incl. time-window/actor edge cases) and forensic-trace API tests (categorized hits, category filter, anomaly reporting, CSV/PDF export, self-audit event content). **54 tests** (previously 37, 17 new), all green.
- **Live Docker verification** (P7-S2b): container built+started, `/healthz`, `/reports/document-volume` (showed real historical data retroactively backfilled, incl. correct `folder_id`), `/reports/storage-usage` (real backend sizes), `/reports/open-workflow-tasks` (correctly empty with no open instances), CSV/PDF export (`text/csv`/`application/pdf`, PDF begins with `%PDF`) verified against the real running stack. Additionally the full scheduling cycle was run through end-to-end with `DMS_REPORT_POLL_INTERVAL_SECONDS` lowered to 15s: schedule → poll tick → storage upload → email via Mailpit → download-link proxy download → `next_run_at` correctly advanced (see `PROGRESS.md` "P7-S2b" for the two edge cases found along the way: the `audit-service` sort-order fix and `notification-service`'s existing recipient validation).

## Open Points

- **License utilization not built** (see above) — to be retrofitted once Phase 9 delivers the License Service.
- ~~No role/permission check on the reporting endpoints~~ — **fixed in Post-Roadmap Phase 19 Session 7** ([ADR 0072](../adr/0072-archival-reporting-rbac.md)): all reporting/scheduling/download endpoints now check `reporting.read`/`reporting.write` via `permission-service`.
- **Ad-hoc exports are not persisted** (deliberate, see above) — a user who wants to share an export link must pass the file along manually; only scheduled runs have a durable download link.
- **A schedule's `recipient_email` must be a real `auth-service` account** — `notification-service` rejects any email to an unknown address with `400` (existing recipient validation since P6-S6, see `docs/services/notification-service.md`). This service itself does **not** validate this upfront when creating a schedule — a typo or an arbitrary external address only surfaces at the next due poll tick (the error is logged, the schedule remains and is retried at the next tick, no status field on the schedule itself indicates the failure). Not communicated in either the backend or the Admin UI — a sensible future improvement would be server-side pre-validation against `auth-service` at `POST /report-schedules`.
- **Forensic trace only covers user/document/folder, not role** (5.4b, since P7-S2c) — see architecture decision above; `permission-service` publishes no events for role CRUD.
- **`document.viewed`/`document.downloaded` only for documents** (5.4b, since P7-S2c) — folder read access remains unaudited, see `docs/services/document-service.md` "Audit depth".
- ~~No role/permission check on `/forensic-trace`~~ — **fixed in Post-Roadmap Phase 19 Session 7** ([ADR 0072](../adr/0072-archival-reporting-rbac.md)): its own, narrower `reporting.forensic_trace` permission instead of `reporting.read`. The previous, unverified `queried_by` parameter has been removed — the self-audit actor source is now exclusively the verified `X-DMS-Principal` header, a caller can no longer falsify the audit entry with a wrong name.
- **Anomaly thresholds only configurable via env variables** — no Admin UI editor for these (the same pattern as other poll intervals in this system); a future need for admin-side adjustment without a restart would be a separate change.
