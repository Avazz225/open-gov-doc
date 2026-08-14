# 0087 — workflow-service: BPMN import review gate via the existing four-eyes mechanism

**Status:** accepted (Session 4 of 4, last session of Phase 21, see `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 21 Session 4, affects `workflow-service`, `process-designer`

## Decision

`POST /process-definitions` (BPMN upload) previously created a new, immediately instance-startable
process definition **instantly and ungated** (aside from the already-existing `admin.object_config` role
check, P6-S6 retrofit) — an uploaded BPMN document can contain script tasks/connector calls ("a real
security concern", `docs/services/workflow-service.md`), so a single admin could activate it unobserved.
This session replicates exactly the already-existing, generic four-eyes recipe from `config-service`'s
P17-S3 retrofit ([ADR 0060](0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md)):
a new action type `workflow.process_definition.import`, checked via `permission-service`'s already-
existing generic approval mechanism ([ADR 0022](0022-four-eyes-approval-via-events.md)).

1. **New `workflow_service.approval_client.ApprovalClient`** — identical pattern to `config_service`'s/
   `document_service`'s class of the same name, a plain `httpx` client against
   `GET /approval-config/{action_type}` and `POST /approval-requests`.
2. **`POST /process-definitions` checks before creation** whether `workflow.process_definition.import`
   currently requires approval. If so: BPMN text + name + optional `process_id` are packed as `payload`
   into a new approval request, and `202` is returned with `{status: "pending_approval",
   approval_request_id}` — **no** creation, no BPMN validation at this point (deliberately deferred, see
   below).
3. **New, pure consumer** (`consumer.py`, `ensure_stream=False`) — reacts to `permission-service`'s
   already-existing `permission.approval.approved` event, filters on
   `action_type == "workflow.process_definition.import"`, then applies the deferred import via the same
   `repository.create_process_definition` used by the immediate path (including the BPMN validation
   already present there).
4. **`process-designer`** (`apps/process-designer`) detects the `202` case and shows a notice instead of
   navigating to the (not-yet-existing) new definition.

## Rationale

- **Why the success case (no approval requirement configured) is NOT wrapped in a status envelope like
  `config-service`**: `config-service`'s `ImportActionResult` uniformly wraps EVERY response
  (`applied`/`pending_approval`) — justifiable there since `POST /config/import` is a comparatively
  rarely used endpoint in tests. `POST /process-definitions`, by contrast, is deeply embedded test
  **infrastructure** in `workflow-service`'s own test suite: over 40 call sites in `test_api.py` (and more
  in `test_federation.py`) first upload a process definition BEFORE the actual subject under test begins
  (instance start, task completion, DMN evaluation, federation, ...) — they have nothing to do with this
  session, they just need a finished definition. A uniform envelope would have touched all of these call
  sites (and `process-designer`'s existing, unchanged success path) even though those tests have nothing
  to do with four-eyes. Instead: the success case stays **byte-identical** to the previous behavior
  (`201` + `ProcessDefinitionOut`), only the new, previously nonexistent `pending_approval` case gets its
  own shape (`202` + `ProcessDefinitionImportResult`) — already distinguishable by HTTP status alone, no
  client needs to change for the success shape.
- **Why BPMN validation happens only when consuming the approved import, not already when creating the
  approval request**: identical rationale to `config_service._apply_config_document`, which likewise
  performs its schema validation only there — the approval request should transport the request as it
  was submitted; rejecting early for invalid BPMN would give the submitter earlier feedback, but would
  complicate the deferral semantics (two different error paths instead of one). Invalid BPMN instead
  fails at consumption time, logged rather than reported to an HTTP caller (no caller remains) — exactly
  `config_service.consumer`'s already-established, broad exception handling.
- **Why `create_process_definition`'s license gate (`_license_gate("write")`) remains UNCHANGED before
  the new approval check**: license status is a deployment/contract question, independent of the
  four-eyes principle — an unlicensed/demo-mode call should not be able to create an approval request in
  the first place.

## Consequences

- **Migration**: none needed (no new field on `ProcessDefinition` — the approval request itself, not a DB
  row, is the "pending" state, identical pattern to `config-service`, where likewise no row exists prior
  to approval).
- **`scripts/run-tests.sh`**: added `workflow-service` to the `CONSUMER_SERVICES` list (services with
  their own NATS consumer whose tests run standalone via an in-process `TestClient`) — previously
  `workflow-service` had no consumer of its own at all (only a producer for its `"workflow"` stream);
  since this session, its test run needs the same container-stop protection as `document-service`/
  `notification-service`/etc., otherwise the running container consumer and the in-process test consumer
  compete for the same durable name.
- **Real regression from an earlier session, found and fixed during this session**: the full
  `workflow-service` test suite (run in full for the first time since post-roadmap Phase 20 Session 5)
  revealed that `test_dispatch_records_delivery_failed_for_unreachable_target`
  (`test_federation.py`) still had the pre-ADR-0081 status expectation (`"delivery_failed"` immediately on
  a single failure) — since ADR 0081, the hub marks a single failure as retry-capable `"pending_retry"`,
  reaching `"delivery_failed"` only after `max_handover_delivery_attempts` is exhausted.
  `workflow-service`'s `federation_task.status` adopts `handover["status"]` unchanged
  (`repository.update_federation_task_status`), so it was technically already correct — only the test had
  silently gone stale since P20-S5, unnoticed because `workflow-service`'s tests had never run in full
  since. Fixed by adjusting the test expectation to `"pending_retry"`.
- **Tests**: `workflow-service` 177 (previously 170, +7: new `test_consumer.py` with 5 tests, one new API
  integration test against the real `permission-service`, plus the regression fix described above).
  `process-designer`: existing 39 tests remain green unchanged (no dedicated new test for the
  `pending_approval` branch of the `designer/page.tsx` save function — this save flow already had no test
  coverage before this session; `tsc`/`eslint`/`next build` confirm type correctness, no real browser
  available in this development environment for a visual check, see `docs/services/admin-ui.md`
  "No browser..." for the same project-wide limitation).
- **Verified live against the actual running stack** (image rebuild + restart of `workflow-service`): a
  test principal was live-granted the `domain-admin-config` role, approval requirement for
  `workflow.process_definition.import` was enabled, a real BPMN was uploaded — this returned `202` +
  `pending_approval`, and the process family demonstrably did not yet exist afterward
  (`GET /process-definitions?name=...` empty); after a real `POST .../approve` against the running
  `permission-service`, the definition was actually created by the new consumer within a few seconds
  (`bpmn_process_id` correctly extracted from the BPMN) — confirming the full
  request→approval→consumption loop against real, independently running containers.
- Docs: new [ADR 0087](0087-bpmn-import-review-gate.md), `docs/services/workflow-service.md`
  (API table, new section, "Open Points" marked resolved), `docs/services/process-designer.md`
  (new save behavior) added.
