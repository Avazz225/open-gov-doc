# 0122 — Supervisor/team task oversight view: a read-only composition of existing data

**Status:** accepted (P31-S11, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 11 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #10), affects `reviewer-ui`;
builds on P31-S9's org-hierarchy foundation ([ADR 0120](0120-org-hierarchy-supervisor-dag-groups-as-org-units.md))
and reuses P31-S10's already-enriched task listing ([ADR 0121](0121-dynamic-org-hierarchy-access-grants-task-claim-and-delegation-reuse.md))

## Decision

Unlike P31-S9/S10, this session needed **no new backend endpoint at all** — `GET /supervisor-assignments?
supervisor_principal_id=` (P31-S9) already returns direct reports, and `GET /tasks` (P31-S10) already
enriches every task with `claimed_by`. The session is a pure `reviewer-ui` composition: a new
`TeamTaskList.tsx` component, its own route (`/team/`), and a third tab in `Shell.tsx` alongside the
existing "Aufgaben"/"Freigaben" tabs — read-only, showing every currently open task claimed by one of the
logged-in person's direct reports.

**A load-bearing identity clarification, not a code change**: `TeamTaskList` queries direct reports using
`user.sub` (the real Keycloak subject, exactly what the gateway injects as `X-DMS-Principal`), not
`user.username`. This is the value that must appear in `SupervisorAssignment.supervisor_principal_id` for
P31-S10's org-hierarchy grant to be genuinely *exercisable* in the first place (the deputy side of a
`Delegation` is checked against the real `X-DMS-Principal` when someone acts on it) — this session's
lookup simply reuses that same, already-necessary convention rather than introducing a new one.

## Rationale

- **No backend work needed**: the plan's own wording ("using P31-S9's 'who reports to me' data") already
  anticipated this — P31-S9 built the query surface, P31-S10 (incidentally, for an unrelated reason —
  enriching the task listing for its own claim/grant UI) already put `claimed_by` exactly where this
  session needs it. Composing two already-shipped, already-tested read endpoints client-side is simpler
  and lower-risk than adding a new cross-service filter endpoint in `workflow-service` for a feature that's
  purely presentational.
- **A separate view, not a mode of `TaskList.tsx`**: the plan is explicit ("the existing `TaskList`...
  stays a flat, instance-agnostic list for the current user... this is a *separate*, org-hierarchy-aware
  view alongside it"). `TeamTaskList` is intentionally a different component with a different question
  ("what is my team working on?" vs. "what can I complete?") rather than a filter toggle bolted onto
  `TaskList.tsx`, which would conflate two different audiences and mental models on one screen.
- **Deliberately read-only — no claim/complete/grant actions here**: a manager *viewing* a report's open
  work is not the same as a manager *acting* on it. The two existing mechanisms for acting on someone
  else's behalf (self-service delegation, ADR 0048; the org-hierarchy access grant, ADR 0121) already cover
  that case, and both surface on `TaskList.tsx` itself (the "on behalf of" selector, the claim/grant
  controls) — duplicating action buttons here would mean two places offering the same capability with two
  different discoverability paths, worse than one.
- **Only tasks with a claim are attributable, unclaimed tasks are silently excluded** — a real, honest
  scope limit inherited from P31-S10's own minimal task-claim mechanism: since claiming is optional (no
  task requires a claim to be completed), "every open workflow task across their direct reports" can only
  mean "every open task a direct report has actually claimed" without inventing a stronger, mandatory
  assignment model P31-S10 deliberately chose not to build (ADR 0121: "not a general task-assignment
  feature"). An unclaimed task belonging to nobody in particular has no direct report to attribute it to.
- **Direct reports only, not the full transitive downward chain**: the plan's own wording says "direct
  reports", distinct from P31-S10's `supervisor_chain` (which *is* transitive, upward). P31-S9's
  `get_supervisor_chain` traversal is symmetric and could trivially be extended downward if a future
  session needs "everyone under me, transitively" — not built here since nothing calls for it yet (the same
  scope discipline ADR 0120 already applied when it declined to build a downward "report chain" resolver).
- **`user.sub`, not `user.username`, for the oversight query**: `SupervisorAssignment` rows are admin-typed
  free text with no enforced format (ADR 0120) — in principle either convention could be used for the
  *assignee* side (`principal_id`), since that value is never itself compared against a real header. But
  the *supervisor* side (`supervisor_principal_id`) genuinely must be the real `sub` for P31-S10's grant to
  ever be exercisable by that supervisor (workflow-service's on-behalf-of check compares the deputy against
  the real `X-DMS-Principal`, always `sub`, never `preferred_username` — confirmed directly in
  `gateway-service/main.py`). Since this session's own lookup is "who reports to *me*, the currently
  logged-in supervisor", using `user.sub` costs nothing and stays consistent with what already has to be
  true for the grant feature to work — not a new requirement invented by this session, just this session
  correctly using the identity that was already load-bearing.

## Consequences

- **No admin-ui guidance was added clarifying that `SupervisorAssignment.supervisor_principal_id` should be
  the real Keycloak `sub`, not a typed username** — this remains implicit, discoverable only by reading this
  ADR or by the grant feature quietly failing to be exercisable if set up with the wrong convention. A
  follow-up documentation (or admin-ui hint text) improvement is a reasonable, low-cost future addition, not
  done in this session since it touches P31-S9's already-shipped UI rather than this session's own scope.
- **Unclaimed direct-report work stays invisible to a supervisor** — a direct consequence of the
  claim-based attribution limit above. If a future session wants supervisors to also see *unclaimed* tasks
  their team is generally responsible for (e.g. via BPMN lane/role, not an individual claim), that needs a
  materially different data model than this session builds on.
- **No pagination/volume handling** — `GET /tasks` already returns the full, unfiltered, system-wide list
  to any caller (an existing characteristic of that endpoint, not new here); this view filters it
  client-side. For an installation with very many open tasks this could become a real performance concern
  eventually, but it is the same characteristic `TaskList.tsx` itself already has, not a regression
  introduced by this session.
