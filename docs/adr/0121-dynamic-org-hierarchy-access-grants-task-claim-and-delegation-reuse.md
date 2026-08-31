# 0121 — Dynamic org-hierarchy access grants: a minimal task-claim mechanism, reusing Delegation

**Status:** accepted (P31-S10, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 10 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #7), affects
`workflow-service`, `permission-service`, `reviewer-ui`; builds on P31-S9's org-hierarchy foundation
([ADR 0120](0120-org-hierarchy-supervisor-dag-groups-as-org-units.md))

## Decision

The plan's own wording ("a workflow task can grant its assignee's supervisor, the full supervisor chain,
or the assignee's/creator's org unit temporary access to the case for the task's duration") assumes two
things that turned out not to exist anywhere in this codebase: a **task assignee** (workflow-service has
no assignee concept at all — a "task" is a transient dataclass derived on every request from SpiffWorkflow's
opaque `workflow_state` blob, per ADR 0019) and a **per-case permission-service resource** (`case-service`
only checks `case.read`/`case.write` system-wide at the root; a `Case` has no `folder_id`/resource pointer
of its own). Both gaps were surfaced to the user directly rather than silently worked around, since either
one changes this session's scope materially. **Two decisions were made, both by explicit user choice**:

1. **"Temporary access to the case" is implemented by auto-creating `Delegation` rows** (permission-service,
   ADR 0048), not by inventing per-case RBAC in `case-service`. `workflow-service` already calls
   `GET /delegations/check` at task completion for the "on behalf of" self-service flow — this session's
   grant reuses exactly that enforcement point, just with deputies resolved from org-hierarchy data instead
   of a person naming one deputy themselves.
2. **A minimal task-claim mechanism was built first**, rather than working around the missing assignee by
   accepting an unvalidated `principal_id` at grant time with no persisted meaning. `TaskClaim`
   (`workflow-service`) is deliberately narrow: who is currently working a task, nothing else — no
   reassignment, no queueing, no notifications, no task-assignment feature in general.

New in `permission-service`: `repository.create_org_hierarchy_grant()` resolves the deputy set for a
`grant_kind` (`"supervisor"`/`"supervisor_chain"`/`"org_unit"`, from P31-S9's `SupervisorAssignment`/reused
`Group`) and creates one `Delegation` per deputy via the existing `create_delegation`. `POST`/
`DELETE /org-hierarchy-grants` are new, deliberately ungated endpoints (see "Rationale").

New in `workflow-service`: `TaskClaim` model (keyed by `(instance_id, task_id)`, the same pair
`complete_task` already uses); `POST`/`DELETE /instances/{id}/tasks/{task_id}/claim`;
`POST /instances/{id}/tasks/{task_id}/org-hierarchy-grant` (requires an existing claim — `404` otherwise);
`ReadyTaskOut` gained `claimed_by`/`grant_kind` fields. A claim's granted delegations are revoked
automatically when the claim is released or the task completes (`_revoke_claim_grants`), not only at the
grant's backstop `ends_at`.

New in `reviewer-ui`: `TaskList.tsx` gained claim/release buttons and, once claimed by the logged-in
person, an inline org-hierarchy-grant form inside the existing expandable task-detail row.
`InstanceDetail.tsx` gained a read-only "claimed by" column only (see "Rationale" for why it stops there).

## Rationale

- **Reusing `Delegation` instead of new per-case RBAC**: building real per-case resource nodes in
  `permission-service` (registering every `Case` the way `folder-service` registers folders) would be a
  materially larger, separate RBAC uplift for `case-service` — arguably its own session, not a natural fit
  bundled into "wire up org-hierarchy resolution". `Delegation` already has everything this feature
  actually needs structurally: a time-boxed delegator→deputy relationship, `scope_process_definition_ids`
  (the one scope dimension `workflow-service`'s check genuinely evaluates), and an already-wired
  enforcement point at task completion. The org-hierarchy grant is thus, mechanically, "the same
  `Delegation` shape as ADR 0048's self-service flow, just with the deputy set auto-resolved from org data
  instead of hand-picked" — genuinely additive, not a parallel mechanism.
- **A claim resolves the deputy set FROM someone real, not an unvalidated free-text field**: without a
  claim, `principal_id` in a grant request would be exactly as unvalidated as `TaskCompleteRequest.
  completed_by` already is (a documented, accepted gap for *that* field) — but a grant creates a real,
  enforceable delegation, a materially different consequence from an audit-log annotation. The user chose
  to close this gap properly (a real claim) rather than propagate the same laxness into a new
  access-granting feature.
- **`TaskClaim` keyed by `(instance_id, task_id)`, not a new normalized task table**: ADR 0019's "no
  separate task model, the workflow_state blob is authoritative" stance is preserved — a claim is a small
  side annotation on an address pair that `complete_task` already treats as stable (`spiff_adapter.
  find_ready_task(wf, task_id)` looks tasks up by exactly this ID), not a competing source of truth about
  what tasks exist or their BPMN state.
- **Claim release/task completion actively revoke the grant, not just let `ends_at` lapse**: "for the
  task's duration" is a real duration, not "up to N hours" — `ends_at` is a documented backstop
  (`org_hierarchy_grant_max_duration_hours`, default 72h) against an abandoned claim that's never released
  or completed, not the primary mechanism. Revocation is deliberately best-effort/non-fatal
  (`_revoke_claim_grants` swallows failures) — a missed revocation degrades to "the backstop applies",
  never to "the primary action failed because ancillary cleanup hiccupped".
- **`POST`/`DELETE /org-hierarchy-grants` are ungated, `DELETE` is a SEPARATE endpoint from
  `DELETE /delegations/{id}`**: this project has no internal service-to-service auth anywhere (a
  documented, accepted gap — the same one `GET /delegations/check` already relies on). Reusing
  `DELETE /delegations/{id}` for automated cleanup was considered and rejected: that endpoint requires the
  caller to BE the delegator or hold the delegation-admin role, an identity workflow-service's automated
  cleanup call has no natural way to present (the delegator is the claim's principal, not necessarily
  whoever is currently authenticated when a task completes). A second, purpose-built endpoint keeps the
  human-facing revocation endpoint's authorization completely unchanged while giving the system a
  consistent, equally-ungated path to clean up exactly the grants it created.
- **A second grant request on the same claim replaces the first, not accumulates**: "a workflow task can
  grant ... temporary access" (singular per the plan's own wording) — requesting `supervisor_chain` after
  already granting `supervisor` revokes the earlier delegations first. Prevents a claim from silently
  accumulating an ever-growing set of active deputies across repeated grant requests.
- **`InstanceDetail.tsx` gets a read-only "claimed by" column, not the claim/grant actions themselves**:
  that view is deliberately a lightweight per-instance status display (ADR 0110) that already omits the
  "on behalf of" delegation selector `TaskList.tsx` has — adding claim/grant actions there too would
  duplicate the feature in a second place rather than extend a status view. `TaskList.tsx` (the actual task
  inbox) is where the action belongs.
- **Org units remain exactly P31-S9's decision (reused `Group`)** — this session resolves the still-open
  question ADR 0120 left for it ("how to pick which group counts as 'the' org unit for a principal in
  several") pragmatically: it doesn't. `grant_kind="org_unit"` grants every group the target principal
  belongs to, unioned — consistent with the DAG's own "union every path" semantics from P31-S9, and no
  riskier than granting to a wrong subset would be, since group membership in this project is already a
  deliberately coarse, admin-managed concept (see ADR 0120 "Consequences").

## Consequences

- **`case-service` still has no per-case RBAC** — a real, acknowledged gap this session works around via
  `Delegation`'s process-definition scoping rather than closes. A future session giving cases their own
  `permission-service` resource nodes remains a legitimate, separate piece of work.
- **`TaskClaim` is not a general task-assignment feature** — no reassignment endpoint, no notification on
  claim/release, no queue/backlog view of claimed-but-not-completed work. It exists exactly far enough to
  make "the assignee" a real, resolvable fact for the org-hierarchy grant; extending it into a fuller
  task-management feature is explicitly out of this session's scope.
- **The grant's backstop `ends_at` still allows an abandoned claim's delegation to remain active for up to
  `org_hierarchy_grant_max_duration_hours`** (default 72h) if a task is genuinely never completed or
  released — a bounded, not indefinite, exposure window, the same class of trade-off as every other
  backstop-expiry pattern already in this project (e.g. `WebdavEditToken`'s TTL).
- **No admin visibility/override for active org-hierarchy grants** beyond what `GET /delegations` already
  offers (they're ordinary `Delegation` rows, indistinguishable from a self-service one except by their
  `scope_process_definition_ids` shape) — a dedicated admin view was not part of this session's scope
  (P31-S11's supervisor/team oversight view is the closer fit for surfacing this kind of data, not a new
  page here).
