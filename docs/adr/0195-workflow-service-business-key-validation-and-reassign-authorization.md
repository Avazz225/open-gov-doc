# 0195 — workflow-service: business_key validation at instance creation, supervisor-only task reassignment

**Status:** accepted
**Context:** P66-S2 (Phase 66, "Security/Correctness Quick Wins" — second session of the eighth
gap-analysis round's plan). Two unrelated `workflow-service` fixes, bundled because both close a
stale-premise gap: a docstring/ADR claim that turned out to already be false at the time it was written.

## Decision

**(a) `POST /process-definitions/{id}/instances` now validates `business_key` at creation time.**
Previously accepted any opaque string (or `None`) with zero validation — the plan's own prior-round
framing, and several docstrings/ADR 0131 itself, asserted "no real process type sets `business_key` to a
real document ID yet." That premise was already false when ADR 0131 was written (P32-S2): the
office-addin/libreoffice-addin "start workflow from this open document" feature has unconditionally set
`business_key=documentId` since Phase 14, for any process definition the user picks. If `business_key` is
provided, the handler now reuses the already-existing `_resolve_business_key_scope` helper (previously
only used for delegation-scope resolution) and rejects with `422` if it resolves against neither
case-service nor document-service. `business_key=None` remains valid and unchecked — not every process
type is document/case-linked.

**No additional check at task-completion time.** `business_key` is set once at creation and is never
updated afterward (confirmed: no code path writes to it outside `repository.start_instance`) — validating
once at creation is sufficient.

**(b) `POST .../tasks/{task_id}/reassign` now requires the caller to be the current claimant or their
supervisor.** Previously gated only by the generic `workflow.write` permission (granted to "everyone" by
default) — any caller could reassign any claimed task to anyone, not just the motivating "a supervisor
seeing a report's stuck claim moves it to someone else" scenario (ADR 0145/P31-S11's `TeamTaskList`). The
docstring's own claim, "no such authorization primitive exists anywhere in this project yet," was also
already false: `POST .../org-hierarchy-grant` in the same file already resolves the supervisor
chain via `permission-service` (ADR 0121). Reassignment now additionally requires `x_dms_principal` to be
either the current claimant themselves (self-service handoff) or a supervisor — direct or transitive
chain — of the current claimant, via a new `PermissionServiceClient.is_supervisor_of()` reusing the
existing, read-only `GET /supervisor-chain/{principal_id}` (P31-S9) rather than
`create_org_hierarchy_grant`'s side-effecting endpoint. `403` otherwise.

## Rationale

- **Why hard-reject an unresolvable `business_key` rather than accept-and-log**: this project already has
  a direct precedent for exactly this shape of fix — P64-S1/ADR 0193 built cross-service reference
  validation for object-type-service/document-service/folder-service on the same reasoning ("a reference
  with no FK enforcement across service boundaries should still be validated at the point of creation").
  `business_key` is architecturally the same kind of opaque cross-service reference; treating it
  differently would be an inconsistency, not a deliberate choice.
- **Why the full transitive supervisor chain, not just the direct supervisor**: `GET
  /supervisor-chain/{principal_id}` already returns the full chain (P31-S9's own union-of-all-upward-paths
  semantics) — a skip-level supervisor reassigning a report's-report's task is the same legitimate
  scenario ADR 0145 was motivated by, just one level further up.
- **Why also allow the claimant themselves, not only a supervisor**: this project's delegation model
  already treats "hand off my own work to someone else" as a legitimate self-service action (ADR 0048); a
  claimant reassigning their own claimed task is a narrower, lower-risk case of the same idea, and
  excluding it would have been a stricter behavior change than the finding asked for.
- **Why `is_supervisor_of()` reuses `GET /supervisor-chain/{id}` rather than `create_org_hierarchy_grant`**:
  the latter has a real side effect (auto-creates a `Delegation` row) — using it purely as an
  authorization check would create delegation rows nobody asked for. The plain chain lookup is read-only
  and already exists for exactly this kind of query.
- ~~**Accepted, documented residual**: `workflow-service` has no local `_is_active_superuser` helper at all
  today (unlike `permission-service`/`query-service`/`plugin-orchestration-service`, per ADR 0190's own
  survey) — an activated break-glass superuser is therefore not automatically exempted from the new
  reassignment gate. Wiring up the full superuser-bypass pattern for this service is a separate,
  independently-scoped gap, not attempted here to keep this session's fix narrow and consistent with what
  the plan asked for.~~ — **closed in Post-Roadmap Phase 74 Session 2**: `workflow-service` now has its
  own `AuthServiceClient`/`_is_active_superuser` (same shape as the other three services), applied to
  both this reassignment gate and ADR 0211's completion gate. Live-verified against the real running
  stack: an unrelated caller gets `403`, the actual activated superuser gets `200`.

## Consequences

- `services/workflow-service/src/workflow_service/main.py`: `start_instance` validates `business_key` via
  `_resolve_business_key_scope` before any DB work, `422` on an unresolvable value; `reassign_task` fetches
  the current claim first and requires claimant-or-supervisor before calling `repository.reassign_task_claim`,
  `403` otherwise.
- `services/workflow-service/src/workflow_service/permission_client.py`: new
  `PermissionServiceClient.is_supervisor_of()`.
- Stale "no real process sets this yet" claims corrected: `document_client.py`'s class docstring,
  `_resolve_business_key_scope`'s own docstring, `models.py`'s `ProcessInstance.business_key` docstring,
  and [ADR 0131](0131-delegation-scope-resolution-case-then-document.md) (Decision, Rationale, and
  Consequences sections all had a version of the same false premise).
- New/updated tests: `test_start_instance_with_manual_task_stays_running` now uses a real document as its
  `business_key` (previously an arbitrary unvalidated string); new
  `test_start_instance_with_unresolvable_business_key_is_422`; `test_reassign_task_moves_claim_to_new_principal`
  now reassigns as the claimant themselves; new
  `test_reassign_task_by_a_non_claimant_non_supervisor_is_403`;
  `test_reassign_task_revokes_the_old_org_hierarchy_grant` now reassigns as the already-set-up supervisor
  (a more meaningful test of the new gate than the previous arbitrary test-client identity). 228/229
  passed (228 baseline + 1 net-new test, one pre-existing, unrelated failure — see below).
- **Pre-existing, unrelated test flake identified during this session**: `workflow-service`'s test suite
  has an intermittent failure in the federation/xdomea-dispatch test family
  (`test_dispatch_records_delivery_failed_for_unreachable_target` on one run,
  `test_dispatch_builds_xdomea_package_instead_of_raw_task_data` on another) — confirmed unrelated to this
  session's changes by reverting them entirely (`git stash`) and reproducing the same class of failure
  (a different test in the same family failing) on the unmodified code. Not investigated further here, out
  of scope for this session; flagged for a future session.
- `workflow-service` rebuilt/redeployed. **Live-verified against the real running stack**: `POST
  /process-definitions/{id}/instances` returns `422` for an unresolvable `business_key` and `201` when
  omitted; a claimed task's reassignment by an unrelated bystander returns `403`, by the claimant
  themselves returns `200`.
