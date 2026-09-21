# 0185 — workflow-service: optimistic concurrency for `ProcessInstance.workflow_state`

**Status:** accepted
**Context:** P60-S3 (Phase 60, "High-Severity Findings" — third and last session of the sixth
gap-analysis round's live-code security sweep, closing Phase 60). `ProcessInstance.workflow_state` had no
locking or optimistic-concurrency protection anywhere it was read-modified-written:
`complete_task`, `retry_instance`, and the SLA poll loop's `advance_timers` all did a plain
`session.get()` → deserialize → run SpiffWorkflow engine steps on the in-memory copy → blind
`session.flush()` write-back, with no row lock and no version column — in clear contrast to the same
file's own `pg_advisory_xact_lock` protection already built for `ProcessDefinition`/`DmnDefinition`
versioning (ADR 0096). Concrete failure: a BPMN parallel gateway produces two simultaneously-ready tasks;
two callers complete them back-to-back, both read the same pre-race state, and whichever commits last
silently overwrites the other's completion — even though both HTTP calls returned `200` and both already
published their own `workflow.task.completed` event.

## Decision

**Optimistic concurrency (a new `workflow_version` integer column), not a row lock.** The finding's own
text named both options as a real design decision for this session. Investigated first: SpiffWorkflow's
`taskType=connector_call` service tasks run a **synchronous**, potentially slow outbound HTTP call
(`main._handle_connector_task`, a plain `httpx.Client()`) **inside** `run_ready_steps()`, itself inside the
same critical section between the read and the write-back. A `with_for_update=True` row lock would hold a
real Postgres lock for the full duration of that external call (up to `peer_call_timeout_seconds`,
currently 300s in `migration-service`'s own settings, for the connector calls that route there) — a real
risk of blocking the SLA poll loop and every other concurrent request against the same instance for
minutes, not milliseconds. Optimistic concurrency only checks/increments a version column at the brief
final write-back, never holding a lock across the external call.

- `ProcessInstance.workflow_version` (new `Integer`, `server_default="0"`), wired via SQLAlchemy's
  built-in `__mapper_args__ = {"version_id_col": workflow_version}` — the ORM automatically includes
  `WHERE workflow_version = :expected` on every `UPDATE` it issues for this model and raises
  `sqlalchemy.orm.exc.StaleDataError` if the row count doesn't match, with zero changes needed to the
  three call sites' own `session.flush()` calls. Named `workflow_version`, deliberately **not** `version`,
  to avoid any confusion with the pre-existing, semantically unrelated `ProcessDefinition.version` (BPMN
  process-family versioning, not per-row optimistic concurrency).
- `complete_task`/`retry_instance` catch `StaleDataError` around their `session.flush()` and re-raise as a
  new `repository.ConcurrentModificationError`, mapped to `409` at the API layer
  (`main.complete_task`/`main.retry_instance`) — with an explicit `await session.rollback()` before raising
  (unlike the existing generic `except Exception: await session.commit(); raise` a few lines below, which
  deliberately commits a partial intermediate state per the P12-S2 resumability pattern — a failed flush
  cannot be committed, it must be rolled back).
- `advance_timers` (the SLA poll loop's batched, single-flush-for-all-running-instances path) is
  deliberately **left untranslated** — a `StaleDataError` there propagates to `main._sla_poll_loop`'s
  existing `except Exception: logger.exception(...)` catch, which already tolerates "a single broken blob"
  failing the WHOLE tick and retrying on the next one (that function's own pre-existing docstring). A
  per-instance-continue restructure (so one conflicted instance doesn't also roll back everyone else's
  otherwise-successful timer advances in the same tick) was considered and explicitly deferred: SLA ticks
  repeat every `sla_poll_interval_seconds`, so the cost of losing one whole tick to a rare race is low, and
  the restructure would need per-instance transactions/savepoints — real added complexity for a narrow
  edge case, not undertaken this session.

## Rationale

- **Why not a distributed lock across replicas**: `workflow-service` remains single-replica by default
  (confirmed via `infra/k8s/dms/values.yaml`'s autoscaling scope, which only covers four hot-path
  services, not this one) — this bug is a single-process race between two concurrent requests/the poll
  loop, not a multi-replica problem. The still-correctly-deferred "distributed lock across replicas" item
  from earlier gap-analysis rounds is a distinct, still-not-yet-applicable concern.
- **Real regression test, mechanism-focused rather than through `complete_task`'s own SpiffWorkflow
  logic**: an attempt to reproduce the exact real-world race (two sessions both reading the row, one
  completing a task while the other's stale in-memory `SpiffWorkflow` graph still shows it ready) turned
  out non-deterministic in practice — `spiff_adapter.deserialize`/`find_ready_task`'s behavior across two
  independently-deserialized copies of the identical `workflow_state` JSON did not reliably reproduce the
  same readiness result run-to-run, for reasons not fully diagnosed (extensive debugging ruled out
  SQLAlchemy identity-map staleness as the cause — the stale object and its cached `workflow_version` were
  confirmed correctly held across the race in every run). Rather than ship a flaky test, or spend
  significantly more time chasing a SpiffWorkflow-internal nondeterminism unrelated to this fix, this
  session tests the actual, controllable mechanism instead: one test proves `workflow_version` itself
  raises `StaleDataError` on a genuine two-session race using a simple field mutation (no SpiffWorkflow
  involvement, fully deterministic); a second test proves `complete_task` translates that into
  `ConcurrentModificationError`, using a deterministic out-of-band version bump (a raw `UPDATE` via a
  separate session) instead of a second real session's SpiffWorkflow state. Both together cover the real
  fix (the version column works; `complete_task` correctly translates a conflict) without depending on the
  flaky cross-session SpiffWorkflow behavior.

## Found and fixed in passing (unrelated to this session's own finding)

Running this service's full test suite (not just the new tests) as part of this session's own
verification surfaced three pre-existing, previously-unnoticed regressions from **earlier sessions in this
same gap-analysis round**, all now fixed:

- **`workflow-service`'s own `SignatureServiceClient`** (`signature_client.py`) sent no `X-DMS-Principal`
  header on `GET /signatures/{id}` — broken since P59-S3's authorization gate (ADR 0180) shipped on
  `signature-service`, but never caught because `signature-service`'s own gate obviously wasn't exercised
  from workflow-service's side during that session. This is a **real production bug**, not just a test
  gap: every signature-task completion check in production would have started failing with `401`. Fixed
  by sending a fixed `X-DMS-Principal: workflow-service` default header (the established convention),
  which passes `signature-service`'s `document.read` check via ADR 0149's baseline "everyone" grant.
- **`workflow-service`'s own test fixtures** (`conftest.py`'s `real_signature`/`real_ses_signature`) called
  `signature-service`'s `POST /signatures` directly with no identity headers at all — same P59-S3 gap,
  test-fixture side. Fixed by adding the required `X-DMS-Principal`/`X-DMS-Username` headers.
- **`workflow-service`'s own federation/xdomea-handoff test fixtures** (`test_federation.py`,
  `test_xdomea_handoff.py`) registered throwaway `federation-hub-service` installations with
  `callback_base_url: "http://localhost:1"` — a literal loopback address, now rejected by P60-S1's new SSRF
  guard (ADR 0183). Fixed by switching to `http://unreachable.invalid:1` (a non-resolving RFC 2606 test
  domain, matching `federation-hub-service`'s own test convention exactly), still "syntactically valid,
  guaranteed unreachable" for these tests' actual purpose.

## Consequences

- New endpoints/behavior: `POST /instances/{id}/tasks/{task_id}/complete` and
  `POST /instances/{id}/retry` now `409` on a lost optimistic-concurrency race, instead of silently
  overwriting a concurrent completion.
- `services/workflow-service/src/workflow_service/models.py`: new `workflow_version` column +
  `__mapper_args__`. Additive migration (`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, no Alembic in this
  project) — verified applied against the real running database.
- New tests: `workflow-service` +2
  (`test_process_instance_version_conflict_raises_stale_data_error`,
  `test_complete_task_translates_version_conflict_to_concurrent_modification_error`), plus the three
  incidental fixes above bring three previously-erroring/failing tests back to passing. 218/218 total.
  `ruff` clean (same pre-existing, unrelated repo-wide failures confirmed out of scope again).
- Rebuilt/redeployed. **Live-verified**: confirmed `workflow_version` exists on the real running database's
  `workflow.process_instance` table; the full test suite (which runs against the real Postgres instance,
  not mocked) passing end-to-end is itself the meaningful verification for a persistence-layer
  concurrency fix, more so than a synthetic `curl`-based race would be.

**Phase 60 ("High-Severity Findings") is now fully closed** — all three findings from this round's
live-code security sweep (federation-hub-service SSRF + ocr-service IDOR, archival-service XXE,
workflow-service optimistic concurrency) are fixed, tested, and verified.
