# audit-service

**Responsibility:** Immutable, hash-chained event log — consumes events from all configured producer subjects and makes tampering detectable after the fact (Concept 3.4/5.3).

**Concept Reference:** 3.4, 5.3
**Own Postgres Schema:** `audit` (table `audit_event`)

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/events?limit=100&actor=&on_behalf_of=&subject=&event_type=&since=&until=` | Recorded events, in **descending** chronological order (newest first, since P7-S2b — previously ascending, see below). All filters optional/combinable (5.4b, since P7-S2) — `actor`/`on_behalf_of` (since **P14-S11**, 4.4a)/`subject` exact match, `event_type` exact or NATS wildcard (`"document.>"`), `since`/`until` filters on `occurred_at` |
| `GET` | `/events/verify` | Verifies the hash chain completely, reports `broken_at_id` on tampering |
| `GET` | `/healthz` | Own health check |

## Data Model

`audit_event`: `id` (PK, autoincrement), `event_id` (unique, for idempotency), `event_type`, `occurred_at`, `service_name`, `subject`, `payload` (JSON), `actor` (nullable, since P7-S2 — see below), `on_behalf_of` (nullable, since **P14-S11**, 4.4a — see below), `recorded_at`, `prev_hash`, `hash` (unique). `hash = sha256(prev_hash + canonical_JSON({event_id, event_type, occurred_at, service_name, subject, payload, recorded_at[, actor][, on_behalf_of]}))` — `actor`/`on_behalf_of` each only flow into the canonical JSON for rows AFTER their own cutover point (see "Actor Field & Cutover Versioning" below and "on_behalf_of Field" further below).

`audit_meta`: singleton row (`id=1`, same pattern as `KennzeichenConfig`/`RetentionConfig` in other services) — `actor_field_cutover_id` (the `id` of the last row before the `actor` field was introduced), `on_behalf_of_field_cutover_id` (since P14-S11, same principle, independent value).

## Events

**Consumes:** all subjects from `Settings.subjects` (default `["registry.>", "document.>", "permission.>", "virus_scan.>", "rendering.>", "ocr.>", "workflow.>", "notification.>", "case.>", "auth.>", "signature.>", "favorite.>", "folder.>", "reporting.>", "query.>", "license.>"]` — `document.>` since P3-S2, since 4.2 explicitly requires complete auditing of force-unlock/conflict copy; `permission.>` since P3-S4, since 4.7 explicitly requires complete auditing of scope locks; `virus_scan.>` since P5-S1, since 10.3/5.3 explicitly require auditing of scan results; `rendering.>` since P5-S2, since generated/failed renditions are also part of traceable document processing; `ocr.>` since P5-S3, since 3.9/5.3 explicitly require auditing of OCR results, including `needs_review` cases; `workflow.>`/`notification.>` since P6-S1/P6-S2 (process instance/task lifecycle and delivery attempts respectively); `case.>` since P6-S3 (case lifecycle, 2.3); `auth.>` since **P6-S5**, since 4.6 explicitly requires elevated audit priority for the superuser break-glass lifecycle (request/activation/deactivation — the prioritization itself is not implemented, see [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md) "Consequences"); `signature.>` since **P6-S7**, since electronic signatures (3.10) are cryptographically bound to a specific document version and thus are also part of traceable document processing; `favorite.>` since **P7-S1d**, so favorite changes remain traceable in the audit trail like any other user action; `folder.>` since **P7-S2** — a genuine pre-existing gap discovered during this session's live smoke test: `folder-service` has had its own event stream since P7-S1b, but was never added to this list. Added retroactively, including a backfill of the entire prior folder history (JetStream delivers a new subject the full stream history by default, no manual backfill needed). `reporting.>` added proactively since **P7-S2b** (had no effect at the time, `reporting-service` did not yet publish its own events) — since **P7-S2c** the first actual producer of this stream (`reporting.forensic_trace.queried`, self-audit of forensic trace access, see `docs/services/reporting-service.md`). `query.>` since **P8-S1** — the new query and trace console (`query-service`, 6.1) audits every executed query itself (`query.executed`), added proactively rather than repeating the `folder.>` gap a second time. `license.>` since **P9-S1** — `license-service`'s installation/status change events (9.2) should likewise land in the audit trail. `mail_connector.>` since **P15-S3** — the new `mail-connector` (2.5/3.3) audits receipt/matching/dispatch of external correspondence by the mail room role. New producers are added by adding their subject prefix to the list, without any code change to the consumer itself. (Note: this enumeration had already fallen behind `Settings.subjects` before P15-S3, e.g. `orchestration.>`/`monitoring.>` were missing — out of scope for this session, not corrected retroactively.)

**Publishes:** no events of its own — the Audit Service is a pure consumer/sink.

## Deletion Register Ledger (10.4, P11-S4)

In addition to the hash chain, on `document.force_deleted`/`folder.force_deleted` events, the consumer appends a row to an append-only file (`/deletion-ledger/deletion-register.jsonl`, `deletion_ledger.py`) on a **dedicated Docker volume** (`deletion-ledger-data`) — deliberately kept outside the shared Postgres instance, so that a DB restore (`scripts/restore.sh`) does not roll this register back with it (Concept 10.4: "remains at the current state"). See `docs/operations/backup-restore.md` for the complete deletion reconciliation mechanism.

## Architecture Decision

Consumer without its own stream (`NatsEventBusClient(ensure_stream=False)`) — see [ADR 0001](../adr/0001-eventbus-consumer-without-stream-ownership.md). Durable consumer name `audit-service`, no `deliver_new`, so that it catches up seamlessly after a restart (no gap in the chain).

**Incident & Fix (P3-S2)**: with a second consumed subject (`document.>` alongside `registry.>`), NATS callbacks for different subjects can run concurrently. `append_event` reads the current chain head (`prev_hash`) before the insert — without serialization, two concurrent calls could read the same `prev_hash` and corrupt the chain (uncovered by `test_consumer_integration.py`, which suddenly started failing after `document.>` was added, because `document.*` messages that had already accumulated during the test run were processed concurrently with the injected `registry.*` test event). Fix: `consumer.py` serializes all calls to `append_event` via an in-process `asyncio.Lock` per handler instance — sufficient, since the Audit Service is designed as a single writer for its own chain (no horizontal scaling of multiple instances on the same chain is planned).

## Actor Field & Filter API (5.4b prerequisite, since P7-S2)

**Starting point**: the acting user was previously stored inconsistently under one of many `payload` keys (`deleted_by`, `created_by`, `initiated_by`, `approved_by`, `set_by`, ...) rather than first-class in the event — for the planned forensic trace (5.4b: "all actions by user X"), the raw append-only log is insufficient as-is. The shared `Event` envelope (`libs/dms-eventbus-client`) therefore got a new `actor: str | None` field, which **every** producer service (13 services, 71 call sites) populates when publishing — the username where a human triggered the action, otherwise `"system:<component>"` (e.g. `"system:retention-poll"`, `"system:ocr-service"` — reusing the convention already established before P7-S2). A consumer that itself publishes something in response to another event (e.g. `case-service` on `workflow.instance.completed`) passes through the triggering event's `event.actor` rather than inventing a new value — same causal action, same acting person. For some call sites without any existing action identity (e.g. `document.metadata.updated`, `folder.resource.moved`/`.deleted`, `document.restored`), `actor` deliberately remains `None` — this session only made already-present information first-class, without adding new fields to foreign schemas.

**Cutover versioning instead of recomputing history**: simply adding a new field to the canonical hash JSON would have retroactively broken **every** historical chain verification (the canonical JSON already differs by the additional key, even with `actor: None`). `audit_meta.actor_field_cutover_id` therefore records (set once, on first start after the migration, to the `MAX(id)` of the rows existing at that time, `0` for an empty chain) the `id` from which the field applies — `_hashable_fields()` includes `actor` only for `id > cutover_id`, both when appending new rows and when recomputing in `verify_chain`. Old rows thus remain verifiable with exactly the same field set they were originally hashed with — `GET /events/verify` demonstrably remained `ok: true` with an identical row count after the migration (live smoke test).

**Filter API** (`GET /events`): `actor`/`subject` exact match, `event_type` exact or NATS wildcard convention (`"document.>"` → SQL `LIKE 'document.%'`, the same notation as `Settings.subjects`), `since`/`until` on `occurred_at`. Purely additive query parameters on the existing endpoint, no new endpoint needed — there was no frontend consumer yet that could have broken.

**Sort order fixed (since P7-S2b)**: `list_events` originally sorted by `id` ascending before the `LIMIT` — for a broad, barely filtered query, this returned the **oldest** matches instead of the newest. It was only `reporting-service`'s user activity report (the first caller without a tight `since`/`until` window) that uncovered this: an action performed just moments before was missing from the results, even though it was findable with an `actor` filter. Fix: `order_by(id.desc())` — now returns the newest matches first; no existing test/consumer pinned the old order.

## on_behalf_of Field (delegation during absence, 4.4a, since P14-S11)

Second, independent envelope field following exactly the same cutover versioning principle as `actor` above (see [ADR 0048](../adr/0048-delegation-lives-in-permission-service-no-task-assignee-retrofit.md) for the complete delegation architecture) — `actor` always remains the person actually acting (the delegate), `on_behalf_of` is the person being represented, NEVER a replacement for `actor` (no identity switch, per the concept's wording). Currently the only producer: `workflow-service`'s `workflow.task.completed`/`workflow.instance.completed` on a completion "on behalf of" (see `docs/services/workflow-service.md`).

**Cutover differs technically from the actor cutover**: `audit_meta` already exists (since P7-S2), so the new column `on_behalf_of_field_cutover_id` is NOT created via insert (`ON CONFLICT DO NOTHING`), but added via a one-time ORM attribute update on the already-existing row (`get_on_behalf_of_field_cutover_id()`) — functionally the same result (cutover = `MAX(id)` at the time of the first start after this deploy), just a different mechanism, because the row already exists.

**Filter API**: `GET /events?on_behalf_of=...` — exact match, combinable independently of the `actor` filter (e.g. "all actions performed on behalf of person X", regardless of which delegate performed them).

## Self-Registration (Concept 3.2a, since P4-S1)

Registers itself with the registry at startup (`libs/dms-registry-client`: register, periodic heartbeat, deregister on shutdown) — basis for the API gateway's routing (`docs/services/gateway-service.md`). Opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; without both values the service runs unchanged without discovery.

## Sensors (Concept 10.1)

None yet — follows in Phase 11.

## Tests

- `uv run pytest services/audit-service/tests`: basic hash chain functions (`test_hashchain.py`, unchanged), repository including new cutover tests (timestamp computation, idempotency, verification across the cutover boundary with entries manually constructed as "legacy rows") and filter combinations (`actor`/`subject`/`event_type` exact+wildcard/time window), consumer integration test against real NATS including `actor` roundtrip. **31 tests** (previously 21, +10, since **P14-S11**: `on_behalf_of` storage, own cutover timestamp/idempotency, verification across the on_behalf_of cutover boundary — a more realistic case than the original actor cutover, since here rows already exist WITH `actor` but WITHOUT `on_behalf_of` —, filtering by `on_behalf_of`; before that 21, 9 new, since P7-S2).
- **Live smoke test** (P7-S2): `GET /events/verify` compared before and after the migration — `ok: true` with identical verified row count for the legacy history, see `PROGRESS.md`. New events (e.g. `POST /folders`) correctly showed up with `actor`, `GET /events?actor=...`/`?event_type=folder.>&since=...` returned the expected matches, a system-triggered event showed `actor="system:retention-poll"`.

## Open Points

- Admin UI view of the audit trail (Concept 5.3) follows with the Admin UI (P4-S3).
- **Export for audits (CSV/PDF) and standard reports (5.4a) follow in P7-S2b** (new Reporting Service, read model over the event stream) — this session only delivers the first-class actor/filter foundation needed for that.
- **Forensic trace UI (5.4b) follows in P7-S2c** — builds directly on the filter API built here (compromised account: "all actions by user X from timestamp Y").
- **`actor` remains `None` at some call sites**, since the respective schemas do not yet carry an action identity (e.g. `document.metadata.updated`, `folder.resource.moved`/`.deleted`, `document.restored`/`.retention.updated`) — retrofitting these fields was deliberately not part of P7-S2 (pure first-class-instead-of-ad-hoc retrofit of already-present information, no new fields in foreign schemas).
- **No role check for `GET /events`/`GET /events/verify`** — anyone with network access to the gateway can read the complete audit trail, an identical, pre-existing gap as before.
- **`on_behalf_of` (4.4a, since P14-S11) currently has exactly one producer** (`workflow-service`'s task completion "on behalf of") — remains `None` for every other event schema, exactly the same deliberate boundary as the original `actor` field above (first-class-instead-of-ad-hoc retrofit only where such an action already actually exists).
