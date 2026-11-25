# 0019 — Workflow instance state as a full serialized blob instead of a normalized task table

**Status:** accepted
**Context:** P6-S1 (Workflow Engine foundation, Concept 7.1). `workflow-service` must persist the execution state of every process instance between individual, stateless HTTP requests (Postgres, `dms-db-base`), since SpiffWorkflow itself does not come with its own persistence layer.

## Decision

Every `process_instance` row stores the full JSON blob produced by `SpiffWorkflow.bpmn.serializer.BpmnWorkflowSerializer.serialize_json()` in a single `Text` column (`workflow_state`). There is **no** separate, normalized table for individual tasks/steps. Ready manual/user tasks are determined live on every read by deserializing the blob (`spiff_adapter.ready_manual_tasks()`), not read from a dedicated projection.

## Rationale

- **SpiffWorkflow already possesses the correct BPMN execution semantics** (parallel/exclusive/inclusive gateways, loops, sub-processes, boundary events in later sessions) — a separate, normalized task table would duplicate part of this state and could need to be updated again with every newly supported BPMN construct (e.g. multi-instance tasks), with the risk of the two representations drifting apart.
- **Serialization/deserialization is SpiffWorkflow's own documented way** of continuing a workflow instance across multiple stateless calls (see the `spiff_adapter.py` docstring) — working against this instead of with it would mean building a parallel state-management layer that SpiffWorkflow already solves correctly on its own.
- **Task IDs remain stable across serialization/deserialization** (empirically verified) — this gives the addressability of individual tasks needed by the API (`GET .../tasks`, `POST .../tasks/{id}/complete`) without any custom ID management.
- **No current need for a dedicated projection**: in this foundational implementation there is no cross-instance query such as "all ready tasks across all running instances" (a task-inbox UI exists only from P6-S8/later) — deserializing per instance on demand is sufficiently performant at the current scale.

## Consequences

- A query "all ready tasks across all running instances" (e.g. for a future task-inbox UI) requires deserializing every running instance — no SQL-level filter at the task level is possible. Should an efficient cross-instance query become foreseeably necessary (likely around P6-S8), an additional projection table derived from the blob would need to be added (a pure read-acceleration measure, not a replacement of the blob as the source of truth).
- The blob is largely opaque to `workflow-service` itself — any interpretation runs exclusively through `spiff_adapter.py`, never directly via SQL/JSON operators on the column. A future SpiffWorkflow version bump with a changed serialization format only affects this one module, but may require a migration strategy for already-stored, older blobs (not relevant in this session, since no production data exists yet).
- No field-level audit trail within a task (e.g. "which value was entered for which form field") beyond what ends up in the `workflow.task.completed` event — for detailed traceability, the event payload would need to be extended if needed, not the persistence structure itself.

## Addendum (P95-S1b): the projection this ADR anticipated

P95-S1's load test showed the cost this ADR predicted. `GET /tasks` deserialized every running instance
(about 3,300 in the dev stack), with several queries each, and the SLA poll loop did the same on every
tick. A call took 16.9 s (`docs/operations/performance-ranking.md`).

So the projection named above now exists, as a read accelerator. The blob stays the truth.
- **`workflow.ready_task`:** one row per ready user task (task id, position, name, lane, `taskType`,
  extensions, data, `ready_since`, and the instance's `created_at` for ordering). It is derived only
  from `spiff_adapter.ready_manual_tasks()`.
- **`process_instance.waits_on_timer`:** whether a timer waits anywhere in the engine state.
- **One write path:** both are written only in `repository._save_state`, which every state change goes
  through (start, complete, skip, send back, migrate, retry, timers). It flushes the instance first, so
  the optimistic-concurrency check fires before any index row changes.
- **Readers:**
  - `GET /tasks` reads the index, one query per page with `limit`/`offset` in SQL;
  - the claim-expiry loop reads the index;
  - the SLA poll loop reads only instances with `waits_on_timer` not false.
  
  Write paths (complete, claim, the eligibility checks) still read the blob.
- **Backfill:** instances saved before the index have `waits_on_timer IS NULL`. They are indexed once at
  startup in `FOR UPDATE SKIP LOCKED` batches, without moving `workflow_version`.

The drift risk named in the rationale above is contained by having one write path, and by tests that
compare the index with a full scan after every kind of state change.

**P95-S1f:** the index also records who is responsible for an unclaimed task: a circulation's current step,
otherwise the lane binding. That lets `GET /tasks?eligible=me` filter in SQL. It is still derived:
- written by the same `_save_state`;
- refreshed after route edits;
- recomputed at startup.

Lane bindings belong to a definition version and never change.
