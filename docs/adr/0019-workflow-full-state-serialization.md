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
