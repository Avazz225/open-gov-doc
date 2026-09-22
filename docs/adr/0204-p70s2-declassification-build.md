# 0204 — P70-S2: Document Declassification — Build

**Status:** accepted
**Context:** P70-S2 (Phase 70, second session), building per P70-S1's scoping decision
([ADR 0203](0203-p70s1-declassification-scoping.md)): a new capability distinct from
`admin.deletion_classified`, a mandatory (not configurable) four-eyes gate mirroring
`auth.superuser.activate`'s shape (ADR 0023), reusing the existing `document.classification.changed`
event, single-step-only rank checking, and non-retroactive `DocumentVersion` snapshots. This ADR records
what was actually built, including a real design correction found by this session's own tests.

## What was built

- `permission-service`: new domain `domain-admin-declassification` (capability `admin.declassification`,
  `repository.DOMAIN_ADMIN_ROLES`), and a startup-seeded `ApprovalActionConfig` row for
  `document.classification.declassify` (`requires_approval=True`, `required_permission=
  "admin.declassification"`), mirroring `auth.superuser.activate`'s bootstrap exactly.
- `document-service`: `repository.next_lower_classification_level` (single-step rank-minus-one,
  raises if already unclassified) and `repository.declassify_document` (the ONLY code path that ever
  lowers `Document.classification_level` — no rank check of its own, since the caller already computed
  and validated the single legal target). New `POST /documents/{id}/classification-level/declassify`
  (`_require_declassification_permission` gate, `admin.declassification`) that always creates a pending
  `ApprovalRequest` and returns `{"status": "pending_approval", ...}` — no synchronous branch exists in
  this endpoint at all. `consumer.py` gained `_handle_declassify_approved`, structurally identical to the
  existing `_handle_delete_approved`/`_handle_force_delete_approved` handlers, executed only on
  `permission.approval.approved` for this action type.

## Real deviation found while building: `required_permission` is ONE shared capability, not two

P70-S1's own scoping text read: "the approver must hold a distinct capability from the initiator's
`admin.declassification` — a second person, not just any second click," describing what sounded like two
separate capabilities (an initiator-side one and an approver-side one). Building against the real
`permission-service` code immediately falsified this: `ApprovalActionConfig.required_permission` is a
**single** capability string, and `_require_permission_if_configured` applies it identically to BOTH
`create_approval_request` (checked against `initiated_by`) and `approve_request` (checked against
`approved_by`) — confirmed already accurately documented in `docs/services/permission-service.md`'s own
pre-existing "Four-Eyes Principle" section, which this session's own scoping read past. There is no
mechanism anywhere in this codebase for an approver-only capability distinct from the initiator's.

This was caught immediately and concretely: a first implementation attempt added a second role
(`declassification-approver`/`declassification.approve`) and set `required_permission` to that second
capability. The very first test run failed with a `403` from `POST /approval-requests` itself — the
*initiator*, holding only `admin.declassification`, failed the config's `required_permission` check
before an approver was ever involved. Root-caused via the actual traceback (`ApprovalClient.
create_request` → `httpx.HTTPStatusError: 403`), not guessed.

**Fix**: dropped the second role entirely; `required_permission="admin.declassification"` is the same
capability checked at both points, matching break-glass's `breakglass.approve` shape precisely. The "two
distinct people" guarantee this codebase actually provides comes from `approve_request`'s unconditional
`approved_by == request.initiated_by` rejection — structural, not capability-based. A person who is the
only one holding `admin.declassification` in an installation can request a declassification but can
never approve their own request; an installation wanting genuine dual control simply grants this one
role to at least two real people, the same operational expectation `breakglass-approver` already has.

## A second bug caught by the same test run

The endpoint's first draft passed `initiated_by=payload.changed_by` (an opaque attribution string, same
shape as `ClassificationLevelUpdate.changed_by`) to `create_request`, instead of the actual authenticated
`x_dms_principal` already verified to hold `admin.declassification`. A test using a different value for
`changed_by` than the calling principal exposed this immediately (same `403`, different cause: the wrong
value was being checked). Fixed to pass `x_dms_principal` as `initiated_by`; `changed_by` remains a
separate, opaque payload field carried through to the eventual `document.classification.changed` event
for audit attribution only.

## Verification

`permission-service` 185/185 (unchanged — new `DOMAIN_ADMIN_ROLES` entry derives its own test coverage
from the existing `test_repository.py` assertions that iterate the list itself, no hardcoded count).
`document-service` 424/424 including 10 new tests: six at the API level (401/403/404/409, the
"always defers, never executes synchronously" proof, and a real integration test against the running
`permission-service` proving a second, distinct holder of the same `admin.declassification` capability
can approve — this exact test is what caught both bugs above) and four at the consumer level (successful
single-step lowering with the correct event payload, lowering to `None`/unclassified from the bottom
rung, and two "logged, not raised" resilience tests for an already-removed document / a malformed
payload). Both services rebuilt, redeployed, and live-verified end-to-end against the real running
stack: a real document raised to `GEHEIM`, declassified (`pending_approval`, classification unchanged),
approved by a second, distinct real principal holding the same role, and confirmed lowered to
`VS-VERTRAULICH` after the NATS consumer processed the approval — the full mandatory four-eyes path,
not just its individual pieces.

## Consequences

- `admin.declassification` is now this project's second capability (after `breakglass.approve`) used as
  an `ApprovalActionConfig.required_permission` — a precedent for future mandatory-four-eyes action
  types to follow directly, including the now-corrected understanding that this is always a single
  shared capability, not an initiator/approver pair.
- No code changes needed in `audit-service` — the existing `document.>` wildcard subscription already
  covers the reused `document.classification.changed` event type for both raises and lowerings.
- `DocumentVersion` snapshots remain a point-in-time record of the classification in force at check-in,
  now confirmed non-retroactive for both directions (raise and lower) — an explicit, tested boundary.
