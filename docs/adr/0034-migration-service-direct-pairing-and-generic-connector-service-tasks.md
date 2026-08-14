# 0034 — Migration Service: direct installation pairing + generic connector service tasks in workflow-service

**Status:** accepted
**Context:** P12-S2 (Concept 7.2, "Migration/Transfer"). A transfer runs between two
**installations** of this software (lock → copy to target system → verify → release
in the target system → deletion in the source system after a transition period) and must
**itself run as an auditable, resumable workflow via the Workflow Engine** ("not as a special
case on the side") — unlike `archival-service` (5.6), which deliberately maps the same flow
via a poll loop instead of real BPMN, because 5.6 makes no such requirement. At P12-S0, a
genuine finding was noted: `workflow-service` had no Automatic/Service-Task connector-call
plumbing yet (7.1 names "triggering a connector call" as an example of a Service Task) — this
session had to build it, generically rather than migration-specific. Check-in at session start:
7.2, unlike 7.4 (Federation Hub), names no mediating instance — the user opted for a
**direct installation pair with an API key** instead of hub mediation.

## Decision

**Generic `connector_call` service tasks in `workflow-service`** (`spiff_adapter.py`): a
`bpmn:serviceTask` with `camunda:properties` `taskType=connector_call`/`serviceUrl=...` is mapped,
via `OVERRIDE_PARSER_CLASSES` (an extension point self-documented by SpiffWorkflow's
`BpmnParser`), onto a dedicated `ConnectorServiceTask` spec class, which on `_execute()` calls a
module-wide callback injectable via `register_connector_task_handler()`. `main.py` registers a
handler for this that synchronously executes `httpx.post(serviceUrl, json=task.data)` and merges
the JSON response back into the process data. `serviceUrl` supports `{placeholder}` substitution
from the current process data (`str.format(**data)`), so that e.g. a per-instance `transfer_id`
can flow into the URL without having to generate the BPMN file individually per instance.
Completely generic — `workflow-service` has no knowledge of `migration-service`; any future
service can drive an automatic BPMN step.

**Resumability via `POST /instances/{id}/retry`** (also generic): a failed `connector_call`
moves the task to `ERROR` (SpiffWorkflow's own semantics). The new endpoint resets `ERROR` tasks
back to `READY`/`FUTURE` via `reset_branch()` and re-runs `do_engine_steps()` — retaining the
task data collected so far. `start_instance`/`complete_task`/`retry_instance` in `repository.py`
persist the `workflow_state` blob for this in a `try`/`finally` each (not only after successful
completion) — otherwise there would be no instance row to resume at all if the very first step
failed.

**Caller-determined instance ID** (`ProcessInstanceCreate.instance_id`, optional): the same
motivation as `federation-hub-service`'s `handover_id` (ADR 0028) — a caller that wants to
persist the ID before the actual start needs an ID known in advance. Without this,
`migration-service` would have had no way, upon a failure of the very first automatic step
(e.g. "lock" unreachable), to find the instance that was nonetheless created in `workflow-service`
in order to make a later `/retry` call — this actually occurred before this flow was changed.

**Timer expression instead of a static literal for the deletion deadline**: `migration-service`'s
`bpmn:intermediateCatchEvent` `timeDuration` references the process variable
`retention_duration` (`retention_duration` as a bare identifier instead of a quoted
ISO-8601 literal) — SpiffWorkflow's `DurationTimerEventDefinition.has_fired()` evaluates
`self.expression` through the script engine, verified for real. The already-existing SLA poll
loop (`_sla_poll_loop`/`repository.advance_timers`, P6-S2) fires due timers for **every**
running instance regardless of process type — no new poll infrastructure needed.

**Direct installation pair instead of a hub** (`migration-service`): `paired_installation`
(`id`, `display_name`, `base_url`, `api_key`) is stored — unlike `federation-hub-service`'s
`Installation`, which stores only a hash — in **plain text**: this installation must present the
key both on outgoing calls as the source and verify it on incoming calls as the target
(`hmac.compare_digest`, constant time) — a pure hash would make the first role impossible.
`POST /paired-installations` generates a new key when `api_key` is missing (returned once,
analogous to `federation-hub-service`'s `POST /installations`), or adopts a key already issued
by the counterpart unchanged.

**`asyncio.to_thread()` for every `DmsTreeClient`/`PeerClient` call** (both synchronous, see
`dms_client.py`): a synchronous HTTP call directly in an `async def` endpoint blocks the entire
event-loop thread. In the self-loopback test (the same installation calls itself as the target),
this leads to a real deadlock — the blocking call waits for a response from exactly the thread it
is itself blocking, and which would otherwise process the incoming request. This actually
occurred (`httpx.ReadTimeout`), fixed via `asyncio.to_thread()` (offloading sync work OUT of an
async context — the unproblematic direction, unlike the `asgiref.async_to_sync` deliberately
avoided in P12-S1).

**Explicit `session.commit()` in every step endpoint**: `Depends(get_session)` provides a fresh
`AsyncSession` per request; if FastAPI closes it at the end of a request without a prior
`commit()`, a merely flushed transaction is automatically rolled back. This actually occurred:
all five step endpoints (`lock`/`copy`/`verify`/`release`/`delete-source`) originally only called
`_mark()`'s internal `flush()`, never `commit()` — the entire transfer appeared to run without
errors (every step responded 200), but **none** of the status changes were actually persisted
(the transfer row remained stuck at `"pending"` forever).

## Rationale

- **Generic connector service tasks instead of a migration-specific special solution**: the
  P12-S0 finding referred explicitly to 7.1 (the Workflow Engine in general), not to 7.2 — hard-
  wiring knowledge of `migration-service` into `workflow-service` would have been a shortcut
  leaving the next use case (e.g. records disposal, which per 5.6 is "technically closely related"
  to 7.2) facing exactly the same problem again.
- **Direct pair instead of a hub**: 7.4 describes itself as a complement to "pure migration
  (7.2, which describes a one-time, final transfer)" — a hub would be unnecessary mediation
  infrastructure for a one-time process explicitly configured by an admin.
- **Plain-text API key instead of a hash**: solely because this installation must itself actively
  present the key (source role) — a hash would offer no security benefit for this (the secret
  would have to be available in plain text somewhere regardless), only unnecessarily complicate
  the target-role check.
- **`asyncio.to_thread()` instead of documenting it as a performance limit**: originally planned
  purely as a performance trade-off (see P12-S1's similar cases) — the self-loopback test
  revealed that this is not a mere trade-off here but a genuine deadlock, as soon as source and
  target are the same process/event-loop instance.

## Consequences

- **Deliberate limit: no historical timestamps for migrated versions** — `document-
  service`'s `POST /documents/{id}/versions` sets `created_at`/`created_by` server-side, with no
  parameter to override them. Migrated versions carry the migration timestamp on the target
  installation, not the original one. A "historical import" path would be a standalone, risky
  feature (potential audit-trail dilution) and is deliberately not part of this session.
- **Deliberate limit: `principal_id` remains opaque for copied permissions** — no identity
  matching between the user populations of two installations (7.4's principle "each installation
  remains autonomous with respect to its own data" applies analogously). Works correctly when
  both installations share the same user base; otherwise roles must be manually reconciled after
  migration.
- **Deliberate limit: only the current document version is migrated**, not the full
  version history — looping over all historical versions would have been possible, but was
  deferred for a reference implementation given the timestamp fidelity already missing (see
  above).
- **Deliberate limit: `dry-run-check` only checks reachability/existence of the target folder**,
  not a full object-type/constraint compatibility analysis — 7.2 names the latter as an example
  ("e.g. matching object types present?"); a full schema-comparison engine would be a standalone,
  large feature.
- **Self-loopback test instead of a real two-installation test** — the same limitation already
  established and documented for `federation-hub-service` (a second independent stack cannot
  reasonably be set up in the sandbox).
- **`asyncio.to_thread()` now stands as a precedent**: any future service that makes synchronous
  SDK calls (e.g. `dms-connector-sdk`, for the planned CMIS connector P12-S4) from `async
  def` FastAPI endpoints AND can potentially call itself (self-loopback or real two-way
  installation pairs) must apply the same pattern.
