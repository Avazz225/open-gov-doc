# reviewer-ui

**Responsibility:** Standalone frontend application with a narrow focus on approval tasks only (8, literally: "dedicated reviewer/approval UI (narrow focus on approval tasks only, including four-eyes cases)"), P14-S2. Two areas: a cross-instance task list for ready BPMN manual/signature tasks (`workflow-service`) and a generic four-eyes approval inbox spanning all action types (`permission-service`, 4.3).
**Concept reference:** 8, 7.1, 4.3, 3.10
**No own Postgres schema** — a pure client-side rendered SPA (static export, same pattern as `apps/user-ui`, see [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), no own backend process.
**ADR:** [0041 — Scope + new cross-instance task list, no new authorization layer](../adr/0041-reviewer-ui-migration-console-scope-and-cross-instance-tasks.md)

## Location in the Repo

`apps/reviewer-ui/` — deliberately **not** under `services/` (Node/React toolchain instead of the Python service template, ADR 0006) and deliberately **not** part of the Admin UI (literal concept requirement: "standalone ... UI").

## Pages

| Route | Purpose |
|---|---|
| `/login/` | Login (identical to user-ui/admin-ui/process-designer) |
| `/` | `TaskList` — task inbox, only reachable with a valid session |
| `/approvals/` | `ApprovalList` — four-eyes approval inbox |

`RequireAuth` renders a shared `Shell` (header with tab navigation between both areas, theme switcher, logout) and, before that, a `MaintenanceBanner` (emergency shutdown, 4.8) — deliberately a simple two-part tab bar instead of a full page navigation like `AdminShell` (admin-ui), since this app only has two areas.

## Task Inbox (7.1, `components/TaskList.tsx`)

Consumes the new `GET /tasks` endpoint (`workflow-service`, P14-S2) — the first cross-instance task list in the entire system (previously there was only `GET /instances/{id}/tasks`, which requires an already-known instance ID). Shows per task the name, associated process definition, related object (`business_key`), lane, and an "Edit" button that opens an inline form:

- **Ordinary manual tasks**: only `completed_by` (pre-filled with the logged-in username) + optional additional process data as freeform JSON.
- **Signature tasks** (3.10, recognized by `extensions.taskType === "signature"`, visible via its own badge): additionally a required field for the `signature_id` — must reference a signature that already exists at `signature-service`, matches the task's document, and has a sufficient level, otherwise `workflow-service` rejects with `400` (unchanged backend behavior, see `docs/services/workflow-service.md` "Signature Task").

Federated tasks (`taskType=federated`/`federated_return`, 7.4) do not appear in the list at all — `GET /tasks` already filters them out server-side, since they are completed exclusively automatically via the Federation Hub (a direct completion attempt would return `409` anyway).

**Deputizing for absence (4.4a, since P14-S11)**: the form additionally shows an "On behalf of" selector (`<select>`), but ONLY if `GET /delegations/active-for-deputy/{principal_id}` (`permission-service`, fetched once when the app loads) returns at least one active delegation for the logged-in person — the default option "For myself" corresponds to the previous, unchanged behavior (`on_behalf_of_principal_id` remains `undefined`). When a represented person is selected, `completeTask()` additionally sends `on_behalf_of_principal_id` — actual enforcement (real delegation check, `403` without a matching active delegation) happens server-side in `workflow-service` (see the documentation there and [ADR 0048](../adr/0048-delegation-lives-in-permission-service-no-task-assignee-retrofit.md)); this selection is purely a UX aid. The list shows raw principal IDs, not usernames (the same already-documented gap as with teamspace member lists, `docs/services/user-ui.md`).

## Approval Inbox (4.3, `components/ApprovalList.tsx`)

Consumes `permission-service`'s `GET /approval-requests` **unfiltered by action type** — the first generic UI surface for this API in the entire system (previously only three narrowly filtered individual consumers, see ADR 0041 "Rationale"). Status filter (open/approved/rejected/all, default "open"), the detail view shows the raw `payload` JSON of the request (the UI deliberately has no knowledge of the domain meaning of individual `action_type`s). Approving/rejecting calls `POST .../approve`/`.../reject` with the logged-in username as `approved_by`/`rejected_by` — server-side it continues to be enforced that the initiator and the decider must not be identical (the core four-eyes principle rule, `403` otherwise). **Since Post-Roadmap Phase 22 Session 4**: "Reject" opens an inline form directly below the affected row instead of a native `window.prompt` dialog (freeform field for the optional justification, "Confirm rejection"/"Cancel") — a pure frontend change, `rejectRequest()`'s already-existing optional `reason` parameter is used unchanged, no backend code affected.

## Authorization

**No capability-gated actions in this app** — neither task completion nor approval decisions are bound server-side to a domain-separated admin role (see ADR 0041 "Rationale"). `RequireAuth` only checks whether a valid session exists at all, no `RequireCapability` redirect like in the Admin UI. `getEffectivePermissions` is still fetched (identical pattern to the other apps), but is currently not evaluated anywhere — preparation for a possible later, more targeted restriction.

## Backend Connection

Exclusively via the API Gateway (3.5):

| Action | Gateway call |
|---|---|
| Login | `POST /api/auth-service/login` |
| Identity after login | `GET /api/auth-service/me` |
| Ready tasks across all running instances (new, P14-S2) | `GET /api/workflow-service/tasks` |
| Complete task | `POST /api/workflow-service/instances/{instance_id}/tasks/{task_id}/complete` |
| Active delegations for the logged-in person (new, P14-S11) | `GET /api/permission-service/delegations/active-for-deputy/{principal_id}` |
| List approval requests | `GET /api/permission-service/approval-requests?status=` |
| Approve/Reject | `POST /api/permission-service/approval-requests/{id}/approve\|reject` |
| Read/write theme preference | `GET/PUT /api/auth-service/me/preferences` |
| Emergency shutdown / maintenance mode status | `GET /api/permission-service/maintenance-mode` |

## Theming/i18n/Auth State

Identical provider copy from user-ui/admin-ui/process-designer (`ThemeProvider`, `I18nProvider`, `auth-context.tsx`), own `src/i18n/de.json`, global `dms.tokens` storage key (single installation like process-designer/user-ui, no `InstallationSwitcher` like admin-ui).

## Build & Delivery

Two-stage Docker image (`apps/reviewer-ui/Dockerfile`, `node:22-alpine` build stage → `nginx:alpine` runtime), `NEXT_PUBLIC_GATEWAY_BASE_URL` as a build arg, overridable via `REVIEWER_UI_GATEWAY_BASE_URL` in `infra/.env`. `infra/docker-compose.yml`: port `${REVIEWER_UI_PORT:-3005}:80` — **not** 3003 (already taken by `GRAFANA_PORT`, 10.1, see ADR 0041).

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build` — TypeScript check, ESLint, production-ready static export.
- `npm test` (Vitest + Testing Library, **20 tests**, previously 18 — since **Post-Roadmap Phase 22 Session 4**: `ApprovalList` now has 8 tests instead of 6, the previous `window.prompt` test replaced by three: opening the inline form + rejecting with a justification, rejecting without a justification (empty field → `reason: undefined`), canceling closes the form without an API call): `AuthProvider` (login/logout/session restoration, 4 tests), `TaskList` (empty list, listing with process/related-object context, signature badge + required field, successful completion incl. reload, rejection on invalid JSON in the additional data, since **P14-S11** additionally: no "On behalf of" selector without active delegations, the selector appears populated with at least one active delegation, completing "on behalf of" also sends `onBehalfOfPrincipalId`, 8 tests), `ApprovalList` (default filter "open", empty list, expanding the detail payload, approving as the logged-in user, rejecting with an optional justification via the new inline form, rejecting without a justification, canceling the form, no actions on an already-decided request, 8 tests).
- Verified live against the built container in a real (headless) browser (login, task list incl. edit form, approvals tab incl. status-filter switching, theme switcher to dark — each without console errors; the task list showed real tasks left over from earlier test runs, additional evidence that `GET /tasks` correctly aggregates real data).

## Open Points

- **No server push/no notifications** — a pure pull interface, must be actively reloaded (see ADR 0041 "Consequences").
- **No lane-/role-based pre-selection of the task list** ("only my tasks") — `workflow-service` does not yet enforce BPMN lanes anywhere (an already-documented limitation, `docs/services/workflow-service.md` "Open Points"), every logged-in principal sees the same complete list.
- ~~No own rejection dialog form — `window.prompt` for the optional justification~~ — **fixed in Post-Roadmap Phase 22 Session 4**: an inline form directly in the table row replaces the native dialog, see "Approval Inbox" above.
