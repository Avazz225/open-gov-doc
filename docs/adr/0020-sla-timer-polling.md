# 0020 — Polling instead of push for SLA time monitoring (timer/boundary events)

**Status:** accepted
**Context:** P6-S2 (per-step SLA time monitoring, Concept 7.1). SpiffWorkflow does not fire due timer/boundary events on its own (no background thread, no push mechanism) - a caller must actively call `BpmnWorkflow.refresh_waiting_tasks()` for a due `WAITING` timer task to transition to `READY` and be executed via `do_engine_steps()` (see the `spiff_adapter.py` docstring, verified against the installed version 3.1.2). `workflow-service` therefore needs its own mechanism that regularly triggers this for every running process instance.

## Decision

A single asyncio background task inside `workflow-service`'s `lifespan` (`_sla_poll_loop`) checks, at a fixed interval (`sla_poll_interval_seconds`, default 30s), **all** instances with `status="running"`: deserialize, `spiff_adapter.check_timers()` (wraps `refresh_waiting_tasks()`+`do_engine_steps()`), re-persist the blob, publish fired boundary events as `workflow.task.escalated`. No separate scheduler service, no distributed lock across multiple `workflow-service` instances.

## Rationale

- **No push-capable scheduler exists in the project** - neither SpiffWorkflow nor the project itself comes with a background thread/scheduler. A polling loop within the same process is the simplest mechanism that requires no new infrastructure (Celery Beat, APScheduler, cron container) - consistent with the principle of not introducing abstraction beyond what is needed for a foundational implementation.
- **ADR 0019 already accepted the consequence**: since every instance stores its full state as a serialized blob (no normalized task table), any cross-instance query ("which instances have due timers?") requires deserializing every running instance. A polling tick does exactly that - not a new limitation, but the same one already documented.
- **Precision is explicitly tied to the poll interval**, not to exact timer due-times - acceptable for a foundational implementation, since Concept 7.1 places no real-time requirement on escalation detection (unlike, e.g., a signature deadline).

## Consequences

- **SLA detection delay of up to `sla_poll_interval_seconds`**: a timer that becomes due shortly after a tick is only detected at the next tick. For very short SLA deadlines (below the poll interval), the interval would have to be shortened project-wide - per-process configurable intervals are not provided for.
- **No distributed lock**: if `workflow-service` runs horizontally scaled (multiple replicas), each replica polls the same instances independently and would publish the same `workflow.task.escalated` multiple times for a due timer (Notification Service would deliver the same escalation multiple times). The project currently assumes single-instance deployments throughout (no other service solves this differently); should horizontal scaling of this service become necessary, a leader election or a DB-level lock (`SELECT ... FOR UPDATE SKIP LOCKED` per instance) would need to be added.
- **Every running instance is fully deserialized on every tick**, regardless of whether it even has a waiting timer - with very many concurrently running instances this becomes a scaling bottleneck. A more efficient solution (e.g. a "next due" projection column derived from the blob that could be filtered on) is a possible future optimization, deliberately not pursued ahead of need here (no current need, see ADR 0019).
