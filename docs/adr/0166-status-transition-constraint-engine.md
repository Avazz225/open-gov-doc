# 0166 — Status-transition validation in the constraint engine, gated on `Case.status`

**Status:** accepted
**Context:** P45-S4 (Phase 45, "Dependency-Resolved Functional Completions"), split out of the former
P45-S3 (see [ADR 0165](0165-ocr-review-manual-task-workflow-integration.md)) once research found the
OCR-review and status-transition halves shared no code, no data model, and even the call direction is
reversed. Concept 4.5 (the constraint engine) says its rules are evaluated "on creation, modification,
and status transitions"; concept 7.1 (the workflow engine) says a BPMN process carries a business
object through its steps "including the associated status transitions ... and constraint checks (2.2)
at each step." Neither section ever defined a concrete status vocabulary or named which business
object's status this meant — genuinely greenfield, unlike P45-S3's OCR half, which had an
already-wired trigger point to build on. Research at session start found exactly one real,
already-wired status-transition trigger anywhere in the codebase: `case-service`'s `Case.status`
`"open"`→`"closed"`, driven synchronously by BPMN process completion, and — critically — already
carrying an optional `object_type_id` linking it to object-type-service's schema world.

## Decision

**Added `statusTransitions` to the constraint-engine schema and `object-type-service`'s object type**:
`[{"from": str, "to": str, "requiredAttributes": [str]}]`, same "absence = no restriction" default as
`allowedParentTypes`. `dms_constraint_engine.validate()` gained `from_status`/`to_status` parameters
(both `None` by default — unchanged behavior for every existing caller); when given, it evaluates every
matching `statusTransitions` entry and reports a missing/empty `requiredAttributes` entry exactly like
a plain `required` attribute check, just scoped to that specific transition. `POST /object-types/{id}
/validate` gained matching optional `from_status`/`to_status` body fields. `requiredAttributes` may
only reference attribute names that actually belong to the object type (422 otherwise, same
"no orphaned field references" check `object_type_layout`'s attribute references already use).

**Gated `case-service`'s `Case.status` "open"→"closed" transition through this new mechanism** — the
one real, concrete integration point this session actually builds, not a spec left for the future.
`status_transitions.close_with_validation()` (new module) wraps BOTH existing closure call sites
(`main.py`'s synchronous branch for a fully-automated process, `consumer.py`'s
`workflow.instance.completed` handler) with a call to `object_type_client.validate(..., status_transition
={"from": "open", "to": "closed"})` before actually closing. On rejection, the case stays `"open"`
(the BPMN instance itself still completes normally — only the case's own status field is gated) and a
new `case.close_blocked` event is published with the constraint engine's error messages.

## Rationale

- **Why `Case.status`, not a new field on `Document`**: `Document` has no status column at all today —
  building one from scratch with no BPMN process driving it would be pure speculation, unlike `Case`,
  which already has a real, workflow-driven transition happening in production code. Grounding the
  first implementation in an already-real trigger, rather than inventing both the trigger and the
  validation in the same session, keeps the design honest about what's actually being solved.
- **Why `requiredAttributes` as the only rule type, mirroring plain `required` instead of a richer
  `if`/`then` grammar**: 4.5's own text lists "required fields" among the rule types it expects at
  minimum, just retargeted from unconditional to transition-scoped — the smallest change that
  satisfies the concept's literal wording without inventing new operators the concept never asked for.
  A future session can extend this the same way `conditions`' `if`/`then` was extended for regular
  validation, if a real need for richer transition rules ever appears.
- **Why gate the transition instead of the BPMN instance itself**: 7.1 talks about "the business object"
  carrying its own status transitions through workflow steps — the workflow engine's own instance/task
  status (`ProcessInstance.status`) is a separate, purely internal SpiffWorkflow bookkeeping concept
  that no external service should be able to block (a stuck BPMN instance would be a much larger
  correctness problem than a case staying open). Blocking only the case's own status field keeps the
  blast radius of a rejected transition local to the one thing the rule is actually about.
- **Why no automatic retry when a close is blocked**: `Case.attributes` is immutable after creation —
  there is no `PATCH /cases/{id}` endpoint, and building one was out of this session's scope (a status-
  transition validation mechanism, not a case-editing feature). This means an object type declaring a
  `requiredAttributes` rule for this transition effectively requires the caller to supply that
  attribute already at `POST /cases` time for auto-closure to ever succeed. Accepted as a known
  limitation rather than solved by adding case-editing capability that wasn't otherwise needed.
- **Why `case.close_blocked` as a new event instead of an HTTP error**: both closure call sites are
  triggered internally (a BPMN completion callback and an async NATS consumer), not a live end-user
  request that could receive a `4xx` response — an event is this project's own established mechanism
  for making an internal decision auditable and visible (same shape as every other domain event on the
  `case` stream, automatically covered by `audit-service`'s existing `case.>` wildcard subscription, no
  new consumer wiring needed).
- **Why this stays scoped to `case-service` only, not rolled out to `document-service`/`folder-service`
  pre-emptively**: those services have no status concept today (confirmed by this session's own
  research) — extending them speculatively, with no real trigger to validate against, would repeat the
  exact mistake this ADR avoided for `Document`.

## Consequences

- **`object-type-service`** gains a new `status_transitions` column, CRUD support, and `/validate`
  parameters — purely additive, no behavior change for any existing caller that doesn't pass
  `from_status`/`to_status`.
- **`libs/dms-constraint-engine`** gains `from_status`/`to_status` parameters on `validate()` — same
  additive, opt-in shape; `document-service`/`folder-service`'s existing calls (which never pass them)
  are completely unaffected.
- **`case-service`** gains a new module (`status_transitions.py`), a new event (`case.close_blocked`),
  and a behavioral change: a case with a `status_transitions`-configured object type and a missing
  required attribute now stays `"open"` where it previously closed unconditionally. This only affects
  installations that actually configure such a rule — the default (`status_transitions: []`) preserves
  every existing case's closure behavior exactly.
- **No admin-UI editor for `status_transitions`** — configured API-only for now, same "backend-first"
  precedent `allowed_parent_types`/`icon` had before their own admin-ui editor arrived later.
- **No retroactive fix for cases already blocked before this feature existed** — not applicable, since
  the feature and the blocking behavior are introduced together; no case could have been blocked before
  this session.
