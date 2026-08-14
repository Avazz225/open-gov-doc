# 0074 — Workflow Instance Start & Task Completion RBAC

**Status:** accepted (Session 9 of 11, see Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 19 Session 9, affects `workflow-service`, `permission-service`,
`case-service`

## Decision

`POST /process-definitions/{id}/instances` (instance start) and
`POST /instances/{id}/tasks/{id}/complete` (task completion) had been **deliberately** open to any
authenticated principal since P6-S6 ("normal business use should not require a domain admin role",
a documented user decision). This session turns this into a real, admin-editable RBAC check instead
of a hardcoded open path:

1. **New `_require_workflow_permission(x_dms_principal, *, access_type)` helper** (`main.py`) — `401`
   without `X-DMS-Principal`, otherwise checks `workflow.write` via `PermissionServiceClient.check`
   (`resource_id="root"`, `access_type="write"`), `403` on rejection. Runs in both endpoints AFTER
   `_reject_during_maintenance` (4.8 remains the outermost lock) and BEFORE all other checks
   (`_require_valid_signature_if_needed`, `_reject_manual_federated_completion`,
   `_require_delegation_if_on_behalf_of`) — basic RBAC first, more specific checks afterward.
2. **`workflow-service`'s local `PermissionServiceClient` (its own copy, not `libs/dms-permission-
   client`) gets a new `check()` method** — same signature as the shared lib, supplementing the
   already existing `has_permission`/`check_delegation`/`is_maintenance_active`. No migration to the
   shared lib (ADR 0066 explicitly designates `check_delegation` as intentionally service-local; a
   full migration would provide no added value here).
3. **"everyone" group (ADR 0067) extended with `workflow.write`** — preserves the previous,
   documented-as-deliberate open behavior, but makes it admin-editable instead of permanently
   hardcoded open.
4. **`case-service`'s `WorkflowClient.start_instance`** (the only real HTTP caller of instance start
   besides migration-service) previously sent no `X-DMS-Principal` header at all — now gets a new
   `x_dms_principal` parameter that passes through the caller already verified by
   `_require_case_permission` (NOT `payload.created_by`, an unverified body field analogous to
   reporting-service's `queried_by` anti-pattern, see ADR 0072).

## Rationale

- **Why `workflow.write` instead of two separate permissions for start/completion**: both are
  equal-ranking, regular business write actions within the same domain (workflow execution) — neither
  is more sensitive than the other (unlike reporting-service's forensic trace vs. standard reports),
  splitting them would be unnecessary granularity with no discernible benefit.
- **Why in the "everyone" group instead of a new domain admin role**: the original P6-S6 decision was
  explicitly *"normal business use should not require a domain admin role"* — a new mandatory role
  would have undone this decision through the back door. "everyone" reproduces exactly the previous
  behavior, but for the first time makes it admin-editable (an admin can in the future remove
  `workflow.write` from "everyone" specifically and grant a narrower role instead, without a code
  change).
- **Why `case-service` passes through the real `x_dms_principal` instead of a synthetic
  `system:case-service` (unlike, e.g., `archival-service`'s `CaseClient`)**: creating a circulation
  folder IS a real action attributable to a human caller — `create_case` already has the verified
  principal on hand from `_require_case_permission`; a synthetic service account would unnecessarily
  dilute the audit trail (unlike purely consumer-driven calls with no human trigger whatsoever, e.g.
  `rendering-service`'s OCR query).
- **`migration-service` needed no change**: its `WorkflowServiceClient` already sends
  `X-DMS-Principal: migration-service` by default (since P12-S2) — "everyone" automatically covers this
  principal without an additional role grant.

## Consequences

- **Tests**: `workflow-service` 171 (test count unchanged, but `client` fixtures in `test_api.py`/
  `test_federation.py`/`test_license_gate.py` (incl. a standalone `TestClient` instance for the
  `raise_server_exceptions=False` test case) now carry an `X-DMS-Principal` header by default; a test
  that specifically proved the 401 path for a missing header within the delegation check now
  explicitly overrides the header to empty — the assertion remains `401` unchanged, only the
  triggering mechanism is now the new basic RBAC check instead of the more specific delegation check).
  `case-service` 50, `migration-service` 8, `permission-service` 128, `config-service` 48,
  `reporting-service` 57 — all unchanged in count, still green. `ruff check`/`ruff format --check`
  clean.
- **Fully verified live against the real running stack** (after rebuilding images for
  `workflow-service`/`case-service` + restart, plus manually re-granting the running "everyone"
  role): `POST /process-definitions/{id}/instances` without header → `401`, with principal → `201`.
  `case-service`'s own test suite already covers the real end-to-end path (`create_case` →
  `WorkflowClient.start_instance` with the passed-through principal) — no mocking between the
  services.
- **`POST /instances/{id}/retry` remains deliberately ungated** — the roadmap mandate explicitly named
  only instance start and task completion, not the retry path; out of scope for this session.
- **`created_by`/`completed_by` remain plain, unverified body strings** (unchanged since P6-S1) — this
  session's gating decision only concerns *whether* an action may be performed, not whether the given
  name is accurate.
