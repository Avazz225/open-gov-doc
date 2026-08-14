# 0004 — Storage redundancy: two targets instead of a target set, per-endpoint retry queue

**Status:** accepted
**Context:** Concept 3.6, Session P3-S4

## Decision

Storage redundancy is limited to exactly **two simultaneous targets**
(primary and an optional secondary target, each one of the two existing
backend types `local`/`s3`) instead of a generic list of arbitrarily many
targets. Asynchronous replication does not run via an in-process background
task, but via a retry queue (`object_copy` table) plus an explicit endpoint
`POST /replication/process-pending`, which is called periodically from the
outside (scheduler/cron, in the future possibly the Plugin Orchestration
Service).

## Rationale

- **Two targets instead of a target set**: The concept gives as an example
  "2× S3 from different providers + 1× NFS". A generic target list would
  require multiple instantiation of the same backend type, each with its own
  configuration (multiple S3 endpoints/buckets/credentials) — no
  configuration structure for that exists yet (`Settings` is currently a flat
  model per service, not a list of nested backend configs). With the two
  backend implementations that actually exist (`local`, `s3`), the required
  core semantics (quorum, primary-synchronous/secondary-asynchronous, read
  fallback, fixity per copy) can already be fully and genuinely demonstrated
  end-to-end, without building a significantly larger configuration layer
  upfront. A real n-target list is a later, additive extension (new Settings
  structure + adjusting `build_backends`/`resolve_targets`), not a break of
  the interfaces built now (`replication.py` already works generically with
  `dict[str, StorageBackend]` + `list[str]`).
- **Retry queue per endpoint instead of a background task**: P1-S1 already
  made a deliberate decision against mutating background sweeps (Registry
  Service: outage detection is computed on read). The same reasoning applies
  here in the reverse direction — an in-process `asyncio` background task for
  replication would not be deterministically testable (timing-dependent) and
  would have no clearly defined lifecycle (process restart mid-replication,
  no visible progress). An explicit endpoint is fully deterministically
  testable, can be triggered manually or by an external scheduler, and fits
  conceptually with the Plugin Orchestration Service already planned for
  Phase 10 (schedule-aware placement of exactly this kind of periodic job,
  3.8).
- **Best-effort rollback on quorum failure**: If the configured quorum is not
  reached, partial copies already written successfully are physically
  deleted again (no orphaned bytes without associated metadata); the failed
  targets remain as a diagnostic entry (`status=failed`, `last_error`) in
  `object_copy`.
- **"Alerting" as a log line instead of a notification**: The concept
  requires alerting on the permanent failure of a target. Since the
  Notification Service does not exist until Phase 6, a structured error log
  line is emitted instead after `max_replication_attempts` unsuccessful
  attempts (`status=failed_permanent`) — easily replaced by a real
  notification once the Notification Service exists.

## Consequences

- A third/fourth redundancy target (e.g. two S3 providers simultaneously)
  requires extending `Settings` to a real list of configured backend
  instances (type + credentials per entry) as well as a corresponding
  adjustment to `resolve_targets`/`build_backends` — the core logic in
  `replication.py` does not need to change for this, since it already works
  with arbitrarily many named targets.
- Rebalancing when adding/removing a target from a running target set
  (Concept 3.6, "technically related to the migration process from 7.2") is
  deliberately not part of this session — it presupposes an n-target
  configuration and is independently large enough for a later session.
- The configuration is currently service-wide (one write-strategy/target pair
  for the entire Storage Service), not overridable per object type/folder as
  envisioned in the concept — an override layer would need a connection
  between Object-Type/Folder Service and Storage Service that does not exist
  today.
- Without an external scheduler, `POST /replication/process-pending` remains,
  until one is introduced (Phase 10 at the earliest, Plugin Orchestration
  Service), a manually/externally triggered operation — secondary copies
  remain "pending" until then if nobody calls the endpoint.
