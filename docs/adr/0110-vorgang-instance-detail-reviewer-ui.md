# 0110 — First per-instance detail view ("Vorgang"), placed in reviewer-ui

**Status:** accepted (P29-S3/S4, see `IMPLEMENTATION_PLAN.md`)
**Context:** new post-roadmap feature (authenticated direct links, [ADR 0109](0109-direct-link-url-scheme.md)), affects `apps/reviewer-ui/`, `services/workflow-service/`

## Decision

"Vorgang" — a running process instance users actively work on — maps to
`workflow-service`'s `ProcessInstance`, **not** case-service's `Case`
(corrected during design after the user clarified the term; the two are
unrelated concepts, see the "Correction" note in the Phase 27+ plan). Its
first-ever per-instance detail view (`InstanceDetail.tsx`) is placed in
`reviewer-ui`, reached via `/?instance=<id>` (ADR 0109's query-param
scheme), showing `GET /instances/{id}`'s status/business key/timestamps
plus `GET /instances/{id}/tasks`'s currently-open tasks (with the
already-existing `completeTask` action available directly from the detail
view). Deliberately **no task history section** — `workflow-service`
persists none (`workflow_state` is an opaque blob, ADR 0019); the view
shows only what genuinely exists rather than fabricating a history.

`TaskList.tsx`'s flat, cross-instance task inbox (ADR 0041) gets a new
"Vorgang öffnen" action per row, surfacing the `instance_id` that was
already being sent to `completeTask()` internally but never shown or
linked to the user before this session.

## Rationale

- **reviewer-ui, not admin-ui**: reviewer-ui is already the app regular
  users complete tasks in — no admin-only capability gate, any
  authenticated principal with `workflow.read`/`workflow.write` can use it.
  Placing a "Vorgang" view where users already do the actual work (task
  completion, `TaskList.tsx`) is more coherent than a separate admin-only
  page a regular case worker couldn't reach at all. This directly
  overturned an earlier draft that had proposed admin-ui, made before the
  "Vorgang" term itself was correctly understood.
- **No new routing concept**: `?instance=<id>` on reviewer-ui's existing
  single route (`page.tsx`), same mechanism as `?document=`/`?folder=` in
  user-ui (ADR 0109) — `page.tsx` becomes a small client component holding
  one piece of view state (`openInstanceId`) instead of two separate
  Next.js routes, consistent with this app's existing `output: "export"`
  constraint (ADR 0006).
- **Reusing `completeTask` and the task-completion form UI**:
  `InstanceDetail.tsx`'s task table/completion form is a trimmed copy of
  `TaskList.tsx`'s (no cross-instance columns — process/business key are
  already implied by being on this one instance's page) rather than a new
  abstraction shared between the two - consistent with this project's
  general preference for small, readable duplication over a premature
  shared component for a two-user pattern.
- **Explicitly no task history**: inventing one (e.g. from
  `notification-service`'s `notification.sent` events, which are
  tangentially related) would misrepresent what the system actually
  tracks. The honest scope — current status, current open tasks — matches
  exactly what `GET /instances/{id}`/`GET /instances/{id}/tasks` already
  provide, no new backend capability needed.

## Consequences

- A `/?instance=<id>` link to a `completed` instance still resolves and
  shows its final status, but `listInstanceTasks` naturally returns `[]`
  (no tasks are ever "ready" on a finished instance) — the empty-state
  message reads identically to a running instance with nothing currently
  due, which is a minor, accepted ambiguity (the status field itself
  already disambiguates the two cases for anyone reading the page).
- If a real task-history capability is ever added to `workflow-service`
  (a materially new backend feature, not part of this session), this view
  is the natural place to surface it — no further routing/plumbing
  changes would be needed, only a new section reading from the new
  endpoint.
- `TaskList.tsx`'s table gained a column exclusively for the new "Vorgang
  öffnen" button; existing columns (name/process/business key/lane) are
  unchanged.
