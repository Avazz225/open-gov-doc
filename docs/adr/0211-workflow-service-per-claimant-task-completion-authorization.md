# 0211 — workflow-service: real per-claimant task-completion authorization

**Status:** accepted
**Context:** P73-S1 (Phase 73, "Security & Correctness Hardening" — first build session of the ninth
gap-analysis round's plan, priority finding). `POST /instances/{id}/tasks/{task_id}/complete` previously
checked only the coarse `workflow.write` permission (granted to "everyone" by default, ADR 0067/0074) —
any authenticated principal could complete any ready task system-wide, not just their own claimed one.
BPMN lanes are parsed and displayed (`reviewer-ui`) but never enforced as an authorization boundary.

## Decision

**`complete_task` now enforces claim-based authorization, but only when a claim exists.** A new
`_require_claim_authorization_if_claimed` helper runs after the existing
`_require_delegation_if_on_behalf_of` check and before `repository.complete_task`: if the task has no
`TaskClaim` row, behavior is unchanged (claiming remains optional/informational per `TaskClaim`'s own
docstring, not a completion prerequisite — an unclaimed task stays open to anyone with `workflow.write`).
If a claim exists, the effective principal — `payload.on_behalf_of_principal_id` if a delegated
completion, else the raw `x_dms_principal` — must be either the claim's own `principal_id` or a supervisor
of it (`PermissionServiceClient.is_supervisor_of`, the exact precedent `reassign_task` already established
at P66-S2/ADR 0195), else `403`.

**Composes with the existing on-behalf-of delegation check, doesn't duplicate it.**
`_require_delegation_if_on_behalf_of` already verifies, against `permission-service`, that `x_dms_principal`
holds a real delegation to act for `on_behalf_of_principal_id` on this process. Once that's established,
the new check treats `on_behalf_of_principal_id` — not the deputy caller — as the identity that must match
the claim or its supervisor chain: a legitimate deputy can complete work claimed by the person they're
standing in for, without needing the claim to literally belong to the deputy's own principal ID.

**`completed_by` stays exactly as free-text as before** — this session doesn't touch it. The new check is
purely an authorization gate on the verified `x_dms_principal`/`on_behalf_of_principal_id`, the same
separation of concerns `_require_delegation_if_on_behalf_of`'s own docstring already documents.

## `folder-service`'s `created_by`/`deleted_by`: investigated, premise found stale, no change made

The plan bundled in "folder-service's `created_by`/`deleted_by` migration from spoofable client-supplied
fields to the `X-DMS-Principal` convention" as a smaller, same-shaped fix. Implementation was started
(schema fields removed, `main.py` call sites switched to derive both fields from `x_dms_principal`) and
then **reverted** after live tracing exposed a real regression: `teamspace-service`'s
`FolderServiceClient.create_folder` deliberately calls `folder-service` under its own fixed technical
identity (`X-DMS-Principal: teamspace-service`, ADR 0149) while passing the *actual human* who created the
teamspace as the `created_by` body field — `teamspace-service/main.py`'s `create_teamspace` threads its own
verified `x_dms_principal` (the real, gateway-forwarded caller) through as that value. Forcing
`created_by` to always equal the HTTP-level `x_dms_principal` would have silently reattributed every
teamspace-created folder to `"teamspace-service"` instead of its real creator — a genuine audit-trail
regression, not a fix.

This is the same "trusted intermediary asserts a different attribution than its own connection identity"
pattern this project already established deliberately at `document-service`'s `created_by`
(`create_document`, ADR 0149's own broad RBAC retrofit added real `document.write` *authorization* via
`x_dms_principal` but explicitly left `created_by` as a separate, unvalidated attribution field) and
`workflow-service`'s own `completed_by` (this same file, `_require_delegation_if_on_behalf_of`'s
docstring). `folder-service`'s `create_folder`/`trash_folder`/`create_folder_template`/
`apply_folder_template` already have real `x_dms_principal`-based authorization
(`_require_folder_permission`/`_require_folder_delete_permission`, both 401-on-missing-header) — the same
authorization layer document-service's endpoints have. The only thing "spoofable" is the attribution
label, and that is an accepted, already-precedented trade-off elsewhere in this codebase, not a unique
folder-service gap.

**Decision: leave `folder-service`'s `created_by`/`deleted_by` as client-supplied fields, unchanged.**
Building an allowlist of "trusted technical identities permitted to assert a third-party `created_by`"
would be a heavier, novel mechanism with no precedent anywhere else in this project, for a risk this
project has already, repeatedly, knowingly accepted elsewhere. Revisiting this decision only makes sense
if a new, different trigger appears (e.g., an actual incident traced to a spoofed `created_by`) — not
speculatively.

## Rationale

- **Why claimed-only, not "always require a claim"**: `TaskClaim`'s own docstring (Post-Roadmap Phase 31
  Session 10, extended by ADR 0145) is explicit that claiming was deliberately never built as a general
  assignment prerequisite — making it one now would be a materially larger, unasked-for behavior change
  reaching every existing unclaimed-task workflow across `reviewer-ui`.
- **Why reuse `reassign_task`'s claimant-or-supervisor check rather than invent a new model**: it already
  solves the identical shape of problem (`404` if unclaimed doesn't apply here since completion of an
  unclaimed task is legitimate, but the claimant-or-supervisor test for a *claimed* task is exactly right),
  and `is_supervisor_of()` already exists on `PermissionServiceClient` from that same P66-S2 fix — no new
  primitive needed.
- **Why the on-behalf-of principal, not the deputy, is checked against the claim**: the delegation
  mechanism's entire point (4.4a, P14-S11) is deputizing during someone's absence — the represented
  principal is the one whose claimed work is being completed; checking the deputy's own ID against the
  claim would make delegated completion of a colleague's claimed task impossible even when the delegation
  itself is valid, defeating the feature.
- **Accepted, documented residual**: same as ADR 0195 — `workflow-service` has no local superuser-bypass
  helper; an activated break-glass superuser is not automatically exempted from this new gate either.
  Already tracked as a separate, independently-scoped gap (Phase 74 Session 2 extends ADR 0190's bypass to
  `workflow-service`'s reassignment gate; this new completion gate should be folded into that same future
  session rather than duplicating the decision here).
- **Why `document-service`'s matching `trash_document`/`create_document` `deleted_by`/`created_by` gap
  wasn't also closed this session**: identical shape, identical accepted trade-off, but out of this
  session's named scope (`folder-service` only) — noted here so it isn't rediscovered as a "new" finding
  by a future gap-analysis round without this context.

## Consequences

- `services/workflow-service/src/workflow_service/main.py`: new
  `_require_claim_authorization_if_claimed`, called from `complete_task` after
  `_require_delegation_if_on_behalf_of`.
- `services/workflow-service/tests/test_api.py`: new
  `test_complete_claimed_task_by_a_non_claimant_non_supervisor_is_403`,
  `test_complete_claimed_task_by_the_claimant_succeeds`,
  `test_complete_claimed_task_by_claimants_supervisor_succeeds`,
  `test_complete_unclaimed_task_stays_permissive_for_any_caller`;
  `test_completing_task_auto_releases_claim_and_revokes_grant` updated to complete as the claimant with a
  real `X-DMS-Principal` header (previously relied only on the free-text `completed_by`, which the new gate
  now correctly rejects for a claimed task with no matching header).
- `services/folder-service/`: no code change — investigated, reverted, documented above.
- 234/235 passed on the scoped `workflow-service` regression run twice in a row; the one remaining failure
  (`test_dispatch_records_delivery_failed_for_unreachable_target` on one run,
  `test_dispatch_builds_xdomea_package_instead_of_raw_task_data` on the other) is the same pre-existing,
  intermittent federation/xdomea-dispatch flake already documented at ADR 0195 — confirmed unrelated by it
  moving to a different test in the same family between runs with no code change in between.
- `workflow-service` rebuilt/redeployed. **Live-verified against the real running stack**: a claimed task
  completed by an unrelated bystander returns `403` ("Nur der Beanspruchende selbst, dessen
  Vorgesetzte(r), oder eine bevollmächtigte Vertretung dürfen diese beanspruchte Aufgabe abschließen"); the
  same task completed by the claimant themselves returns `200`.
