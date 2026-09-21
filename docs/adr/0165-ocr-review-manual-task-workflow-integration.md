# 0165 — OCR `needs_review` as a real BPMN Manual Task, and splitting P45-S3

**Status:** accepted
**Context:** P45-S3 (Phase 45, "Dependency-Resolved Functional Completions") was planned as one session
covering two independently-deferred integrations: `ocr-service`'s `needs_review` flag getting real
workflow integration, and `object-type-service`'s status-transition validation (Konzept 4.5) getting
the same. Research at the start of this session found the two halves share no code, no data model, and
even the call direction is reversed (OCR: producer→workflow-service; status-transition: workflow-
service→object-type-service, which additionally has no status/status-machine concept anywhere in the
codebase yet — a greenfield design question, not a wiring task). This ADR covers the OCR half only,
built as this session; the status-transition half is deferred to its own future session (renumbered in
`IMPLEMENTATION_PLAN.md`).

## Decision

**Split P45-S3 into two independent sessions** — this session builds only the OCR `needs_review`
integration. The status-transition half is deferred, not abandoned, pending its own session that first
has to decide what a "status" even is in this system before `object-type-service`'s constraint engine
can validate transitions of it.

**OCR review is a real BPMN Manual Task, not the lightweight `approval_client.py` mechanism** already
used elsewhere in this project (`config-service`/`folder-service`/`migration-service`/`workflow-service`
itself, for four-eyes gates on an *action*). A bundled `resources/ocr_review.bpmn` (Start → Manual Task
"OCR-Ergebnis pruefen" → Service Task `connector_call` → End) is idempotently registered at startup
(`WorkflowServiceClient.ensure_process_definition`, exact pattern already established by
`migration-service`'s own client of the same name) and one instance started per `needs_review` result
(`business_key` = the OCR result ID, `initial_data` = `{document_id, ocr_result_id,
average_confidence}`). Completion surfaces automatically in `reviewer-ui`'s already-fully-generic
`GET /tasks` inbox (`TaskList.tsx`) — **no reviewer-ui code change was needed**. The connector Service
Task calls back into a new, deliberately **ungated** `POST /ocr-results/{id}/reviewed` on `ocr-service`
itself (no `X-DMS-Principal` — `workflow-service`'s `_handle_connector_task` sends no principal header
at all, the same precedent as `migration-service`'s own `/transfers/{id}/steps/*` connector targets),
which flips `status` back to `"ready"` and records `reviewed_at`/`reviewed_by` (new nullable columns,
no new status value — `rendering-service`/`search-service`'s existing `"ready"`/`"needs_review"`
handling needs no change).

## Rationale

- **Why a Manual Task over the approval-request mechanism**: `needs_review` means "a human should look
  at this OCR extraction and confirm or note it's wrong" — a review of a *result*, not an approve/reject
  gate on an *action about to happen*. `approval_client.py`'s shape (binary approve/reject before a
  mutating action proceeds) does not fit; a Manual Task that a reviewer picks up, inspects, and marks
  done is the closer semantic match, and it is the ONLY task primitive `workflow-service` actually has
  (`GET /tasks` only ever surfaces Manual/Signature tasks belonging to a running BPMN instance — there is
  no standalone "create a task" endpoint independent of a process instance).
- **Why a bundled, self-registering BPMN file instead of requiring an admin to upload one via
  `process-designer`**: `migration-service` already established this exact pattern (`resources/*.bpmn` +
  `ensure_process_definition()` idempotent upload at startup) for the identical problem — an internal,
  service-owned workflow that must exist unconditionally on every installation, not something an
  operator configures. Copying a proven pattern beat inventing a second one.
- **Why the review data flows through the pre-existing generic "additional data" JSON field instead of
  a new `reviewer-ui` form**: `workflow-service`'s `complete_task` already merges a task's free-form
  `data` into the process's shared data, which the subsequent connector call already forwards verbatim
  to whatever `serviceUrl` it targets — the exact payload shape `POST /ocr-results/{id}/reviewed` needs.
  Building a dedicated OCR-review form in `reviewer-ui` would have duplicated a mechanism that already
  does the job, for a net-new UI surface this session's own scope did not ask for.
- **Why `POST /ocr-results/{id}/reviewed` is ungated**: `workflow-service`'s connector-call handler
  (`_handle_connector_task`) sends a plain `httpx.post(serviceUrl, json=data)` with no headers at all —
  gating this endpoint like every other one would make it permanently unreachable via the only path
  that's actually meant to call it. `migration-service`'s equivalent `/transfers/{id}/steps/*` endpoints
  are ungated for the identical reason; this is that established precedent applied to a second service.
- **Why `status` flips back to `"ready"` instead of a new `"reviewed"` value**: `rendering-service`'s and
  `search-service`'s consumers already treat `"ready"`/`"needs_review"` identically (both index/derive
  text regardless) — a third status value would need both of them updated for no behavioral gain.
  `reviewed_at`/`reviewed_by` are the only record that a review actually happened; nothing downstream
  needs to distinguish "always been ready" from "reviewed into ready".
- **Why the workflow trigger is unconditional, with no de-duplication guard**: `needs_review` results
  are never automatically reprocessed (only `failed` ones are, via the retry poll loop, and manual
  `POST .../retry` requires `failed_permanent`) — the only realistic way `process_version` runs twice for
  the same version is an at-least-once NATS redelivery, an already-accepted class of risk elsewhere in
  this project. A resulting duplicate reviewer-ui task is a harmless UX annoyance, not a data-integrity
  issue, and not worth a dedicated guard.

## Consequences

- **`ocr-service` gains its first `workflow-service` dependency** (`WorkflowServiceClient`, new
  `workflow_service_base_url` setting, new `depends_on: workflow-service` in
  `infra/docker-compose.yml`) and its first self-bootstrapped `permission-service` role assignment
  (`domain-admin-config` → `system:ocr-service`, mirroring `migration-service`'s
  `_ensure_config_admin_permission` exactly).
- **No `reviewer-ui` or `rendering-service`/`search-service` code changes** — the integration is fully
  additive on the `ocr-service`/`workflow-service` side.
- **New Open Point, deliberately accepted**: no document/OCR-text preview is linked from the review task
  itself — a reviewer must independently open the document in `user-ui` to actually judge the OCR
  result before completing the task. `reviewer-ui`'s task table does show the business key (the OCR
  result ID, which embeds the document ID), so the document is at least identifiable.
- ~~**`object-type-service`/status-transition (Konzept 4.5) remains open**, now split out into its own,
  not-yet-numbered future session — first needs a decision on what "status" concretely means as a data
  model before the constraint engine can validate transitions of it.~~ — **closed by ADR 0166** (Phase 45
  Session 4, forked from this session): new `status_transitions` schema on the object type +
  `Case.status` "open"→"closed" gating, the first (and, for now, only) real status-transition trigger.
