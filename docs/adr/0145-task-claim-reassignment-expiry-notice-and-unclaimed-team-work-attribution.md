# 0145 — TaskClaim reassignment, expiry notification, and unclaimed team-work attribution

**Status:** accepted (P35-S3, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 35 Session 3 (org-hierarchy & workflow polish), affects `workflow-service`/`notification-service`/`apps/reviewer-ui`

## Decision

Closes the three gaps ADR 0121 named as its own deliberately deferred future scope ("no reassignment, no
queueing, no notifications") and narrows the "purely read-only" restriction ADR 0122 placed on
`TeamTaskList.tsx`:

1. **`POST /instances/{id}/tasks/{task_id}/reassign`** (`new_principal_id`) replaces the current
   `TaskClaim` row with a fresh one for the new principal — `repository.reassign_task_claim()` deletes the
   old row and inserts a new one rather than updating `principal_id` in place, so `grant_kind`/
   `granted_delegation_ids` do NOT carry over (any org-hierarchy grant tied to the old claim is separately
   revoked via the existing `_revoke_claim_grants()`, same as a release). A reassignment is not "the same
   claim under a new name" — it is closer to release-then-claim, just atomic and without the gap where the
   task would briefly appear unclaimed. Gated by the same `workflow.write` capability as claiming itself
   (no new "must be the supervisor" check — see Rationale).
2. **`_task_claim_expiry_poll_loop`** (new poll loop, same idiom as `_sla_poll_loop`/document-service's
   `_lock_reminder_poll_loop`) finds claims older than `Settings.claim_abandonment_threshold_hours`
   (default 72h — the same number as `org_hierarchy_grant_max_duration_hours`'s existing backstop, but a
   genuinely separate setting, see Rationale) that haven't already been notified
   (`TaskClaim.expiry_notified_at IS NULL`, same "sent once" dedup pattern as `DocumentLock.
  reminder_sent_at`), stamps `expiry_notified_at`, and publishes `workflow.task_claim.abandoned`.
   `notification-service` consumes it with a new `_handle_task_claim_abandoned` handler — a single in-app
   nudge to the claim holder, mirroring `_handle_lock_reminder`'s "closest existing precedent" shape.
3. **`TeamTaskList.tsx`** now also lists a task that is UNCLAIMED but whose process instance was started by
   a direct report (`ReadyTaskWithInstanceOut.created_by`, already returned by `GET /tasks` but previously
   unused by this view) — the only attribution signal an unclaimed task has. A supervisor can assign such a
   task, or reassign an already-claimed one, via a new inline form (same UI idiom as the existing claim/
   grant forms) — but still cannot complete a task from this view; that stays `TaskList.tsx`'s/the existing
   delegation mechanism's territory.

## Rationale

- **"Instance creator" over "BPMN lane/role membership" for unclaimed-work attribution**: the plan's own
  phrasing floated a richer alternative (resolving unclaimed team work from BPMN swimlane/role membership),
  but no such cross-service capability exists anywhere in the project today — BPMN lanes are parsed into
  `extensions`/`lane` purely for display, never resolved against `permission-service`'s org-hierarchy data.
  Building that resolution would be a genuinely separate, larger feature, not "closing ADR 0121's deferred
  scope." `ProcessInstance.created_by` is already a real, populated field with an existing precedent for
  the exact same "assignee vs. creator" choice (`org_unit_of: "assignee"|"creator"` on the org-hierarchy
  grant endpoint, ADR 0121) — reusing it costs no new data model and no new cross-service lookup. Confirmed
  with the user via `AskUserQuestion` before implementing (explicit answer: "Instance creator").
- **Reassignment authorization: same `workflow.write` gate as claiming, no new "must be supervisor" check**
  — no authorization primitive for "is caller the direct supervisor of the claim holder" exists anywhere in
  this project (ADR 0074's `workflow.write` is a flat capability, not relationship-aware), and claiming
  itself has never been restricted to a specific assignee (`POST .../claim`'s `principal_id` is an explicit
  body field precisely so an admin can claim on someone else's behalf, per ADR 0121). Adding a
  supervisor-only restriction to reassignment alone would be a new, asymmetric authorization concept
  invented in this session rather than an extension of an existing one — left for a future session if a
  real need for it surfaces (`TeamTaskList.tsx` is reachable only via the reviewer-ui `/team` route, itself
  gated only by authentication like every other route in that app, unchanged from ADR 0122).
- **Reassignment deletes-and-recreates rather than updating `principal_id` in place**: an org-hierarchy
  grant is resolved FROM a specific claim's principal (ADR 0121's "resolve a supervisor/org unit from the
  assignee"); silently repointing an existing grant to a new assignee without re-resolving it would leave a
  stale, unintended set of deputies with access. Forcing a fresh claim (no grant) after reassignment matches
  the existing release/re-claim behavior exactly, just atomic and without the task's `claimed_by` briefly
  going `null`.
- **`claim_abandonment_threshold_hours` is a genuinely separate setting from
  `org_hierarchy_grant_max_duration_hours`, despite sharing the same 72h default**: they answer different
  questions — one is "when does an unrevoked delegation stop granting access" (a hard security backstop,
  unrelated to whether anyone noticed), the other is "when should the claim holder be reminded they still
  have this" (a soft nudge, independent of whether a grant was ever requested for the claim at all — most
  claims never call `POST .../org-hierarchy-grant`). Coupling them would silently break the moment either
  default is tuned independently for its own reason.
- **In-app-only notification, no email**: mirrors `document.lock.reminder`'s precedent exactly (the closest
  existing "you're still holding something, unrenewed/uncompleted" case) — the claim holder is the one
  in-app recipient, with an email possible only indirectly through a configured `EmailTemplate` domain
  match, same mechanism, no new channel logic invented.
- **Narrowing (not reversing) ADR 0122's "purely read-only" stance**: ADR 0122 explicitly framed the
  restriction as "a manager viewing a report's work is not the same as acting on it" — this session adds
  exactly the two actions ADR 0121 itself had already flagged as its own future scope (reassign, and now
  also assign an unclaimed task), while deliberately keeping task completion out of this view, preserving
  the original distinction between "oversight" and "acting on someone else's behalf" (which remains the
  existing on-behalf-of delegation's territory, unchanged).

## Consequences

- **Tests**: `workflow-service` 207 tests (up from 198, +9) — `test_repository.py` covers
  `reassign_task_claim` (old claim gone, new claim has no carried-over `grant_kind`) and
  `list_claims_due_for_expiry_notice` (threshold filtering, already-notified claims excluded); `test_api.py`
  covers the reassign endpoint (success, `404` for an unclaimed task) and `created_by` appearing on
  `GET /tasks`. `notification-service` 81 tests (up from 78, +3) — `test_consumer.py` covers the new
  handler's fallback body, a configured-template override, and the direct-link append. `reviewer-ui` 44
  tests (up from 41, +3 net: `team-task-list.test.tsx` grew from 4 to 7 — one new fixture distinguishing an
  unclaimed task from a stranger's instance (still excluded) from an unclaimed task from a direct report's
  instance (now included), plus assign and reassign interaction tests).
- **Live-verified against the real, rebuilt running stack**: a real `SupervisorAssignment` (keyed by the
  supervisor's actual Keycloak `sub`, not username — see the debugging note below), a real process instance
  claimed by a direct report, a real `workflow.task_claim.abandoned` event published and confirmed to
  produce a real in-app notification, and a full Playwright pass through `/team`: the claimed task's row
  renders, "Neu zuweisen" opens the inline form, submitting reassigns the claim, and the row updates to the
  new claimant after reload (screenshots captured before/after).
- **A real regression discovered in this session's own workflow-service test suite, unrelated to this
  session's own code**: P35-S1's `is_org_unit` requirement on `POST /groups` had not been retrofitted into
  workflow-service's own `_create_group_with_members()` test helper (only permission-service's and
  admin-ui's own tests were fixed in P35-S1), silently breaking
  `test_org_hierarchy_grant_org_unit_of_creator_resolves_from_instance_creator`. Fixed in this session as a
  drive-by (the helper's only call site).
- **`created_by` on `GET /tasks`/`GET /instances/{id}/tasks` is a response-shape addition, not a new
  capability** — every existing consumer of these endpoints already receives the field; only
  `TeamTaskList.tsx` newly reads it.
- **No queueing/prioritization of unclaimed team work** — `TeamTaskList.tsx` lists unclaimed team-created
  tasks in whatever order `GET /tasks` returns them, same as `TaskList.tsx`'s own unordered list; explicitly
  out of this session's scope, unchanged from ADR 0121's own framing of "closing exactly these three named
  gaps," not a general work-distribution feature.
