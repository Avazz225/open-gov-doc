# reviewer-ui

**Responsibility:** Standalone frontend application with a narrow focus on approval tasks only (8, literally: "dedicated reviewer/approval UI (narrow focus on approval tasks only, including four-eyes cases)"), P14-S2. Two areas: a cross-instance task list for ready BPMN manual/signature tasks (`workflow-service`) and a generic four-eyes approval inbox spanning all action types (`permission-service`, 4.3). Since Post-Roadmap Phase 31 Session 11, a third area: a supervisor/team task oversight view (14.2) — originally purely read-only, since Post-Roadmap Phase 35 Session 3 additionally offering assign/reassign actions (not completion, see below). Since Post-Roadmap Phase 74 Session 1, a fourth area: a case-browsing view (14.2, ADR 0141's own named gap) giving a reviewer case-context for the task they're working on.
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
| `/team/` | `TeamTaskList` — supervisor/team task oversight (Post-Roadmap Phase 31 Session 11, [ADR 0122](../adr/0122-supervisor-team-task-oversight-view-read-only-composition.md)) |
| `/cases/` | `CasesPane` — case browsing (Post-Roadmap Phase 74 Session 1, ADR 0141) |

`RequireAuth` renders a shared `Shell` (header with tab navigation between all areas, theme switcher, logout) and, before that, a `MaintenanceBanner` (emergency shutdown, 4.8) — a simple tab bar instead of a full page navigation like `AdminShell` (admin-ui). Originally two tabs (ADR 0041), a third ("Team") added in Post-Roadmap Phase 31 Session 11, a fourth ("Vorgänge") added in Post-Roadmap Phase 74 Session 1.

## Task Inbox (7.1, `components/TaskList.tsx`)

Consumes the new `GET /tasks` endpoint (`workflow-service`, P14-S2) — the first cross-instance task list in the entire system (previously there was only `GET /instances/{id}/tasks`, which requires an already-known instance ID). Shows per task the name, associated process definition, related object (`business_key`), lane, and an "Edit" button that opens an inline form:

- **Ordinary manual tasks**: only `completed_by` (pre-filled with the logged-in username) + optional additional process data as freeform JSON.
- **Signature tasks** (3.10, recognized by `extensions.taskType === "signature"`, visible via its own badge): additionally a required field for the `signature_id` — must reference a signature that already exists at `signature-service`, matches the task's document, and has a sufficient level, otherwise `workflow-service` rejects with `400` (unchanged backend behavior, see `docs/services/workflow-service.md` "Signature Task").

**Since Phase 45 Session 3**: `ocr-service` became this list's second-ever OCR/document-adjacent producer, starting one instance of its own bundled `ocr_review.bpmn` per `needs_review` OCR result — an ordinary Manual Task, no `reviewer-ui` code change needed. A reviewer identifies which document it's about via the row's `business_key` column (the OCR result ID, `{document_id}:{version_number}`) and types e.g. `{"reviewed_by": "alice"}` into the same generic additional-data JSON field already used for other task types; that data flows straight into `ocr-service`'s `POST /ocr-results/{id}/reviewed` callback (see `docs/services/ocr-service.md`). No document/OCR-text preview is shown here — a reviewer must separately open the document in `user-ui` to actually judge the OCR result.

Federated tasks (`taskType=federated`/`federated_return`, 7.4) do not appear in the list at all — `GET /tasks` already filters them out server-side, since they are completed exclusively automatically via the Federation Hub (a direct completion attempt would return `409` anyway).

**Deputizing for absence (4.4a, since P14-S11)**: the form additionally shows an "On behalf of" selector (`<select>`), but ONLY if `GET /delegations/active-for-deputy/{principal_id}` (`permission-service`, fetched once when the app loads) returns at least one active delegation for the logged-in person — the default option "For myself" corresponds to the previous, unchanged behavior (`on_behalf_of_principal_id` remains `undefined`). When a represented person is selected, `completeTask()` additionally sends `on_behalf_of_principal_id` — actual enforcement (real delegation check, `403` without a matching active delegation) happens server-side in `workflow-service` (see the documentation there and [ADR 0048](../adr/0048-delegation-lives-in-permission-service-no-task-assignee-retrofit.md)); this selection is purely a UX aid. The list shows raw principal IDs, not usernames (the same already-documented gap as with teamspace member lists, `docs/services/user-ui.md`).

**Task claim & dynamic org-hierarchy access grants (14.2, Post-Roadmap Phase 31 Session 10, [ADR 0121](../adr/0121-dynamic-org-hierarchy-access-grants-task-claim-and-delegation-reuse.md))**: a new "Beanspruchung" column shows `task.claimed_by` — a "Beanspruchen" button on an unclaimed task (`claimTask()`, `principal_id` defaults to `user?.username`, same convention as `completedBy`), or, once claimed, the claimant's name plus a "Freigeben" button visible only to whoever holds the claim (`task.claimed_by === user?.username`). Once claimed by the logged-in person, the existing expandable detail row additionally shows a grant form (`aria-label="Zugriffsfreigabe erteilen"`, distinct from and rendered above the completion form) — a `grant_kind` select (direct supervisor / full supervisor chain / org unit, the latter with a second select for whose org unit, assignee or creator) submitting via `createTaskOrgHierarchyGrant()`. The response's `deputy_principal_ids` are shown inline ("Freigabe erteilt an: ..."), including the empty case (no supervisor/group configured for that principal) — the same graceful-empty-result posture the backend uses throughout. `InstanceDetail.tsx` shows the same "Beanspruchung" column but read-only — no claim/grant actions there, consistent with it already omitting the "on behalf of" selector above (a deliberately lightweight per-instance status view, ADR 0110).

## "Vorgang" Direct Links & Instance Detail (Post-Roadmap Phase 29, [ADR 0109](../adr/0109-direct-link-url-scheme.md)/[ADR 0110](../adr/0110-vorgang-instance-detail-reviewer-ui.md))

`/?instance=<id>` on the same root route (`page.tsx`, now a small client component holding one piece of view state instead of purely composing `TaskList`) opens `InstanceDetail.tsx` — the first UI anywhere addressing a single `workflow-service` process instance by ID. Shows `GET /instances/{id}` (status/business key/timestamps) and `GET /instances/{id}/tasks` (currently-open tasks, with the same completion form as `TaskList.tsx`, trimmed to this one instance). Deliberately no task history section — `workflow-service` persists none (ADR 0019). `TaskList.tsx` itself gained a "Vorgang öffnen" button per row, surfacing the `instance_id` it already used internally for `completeTask()` but never showed before this session.

## Supervisor/Team Task Oversight (14.2, Post-Roadmap Phase 31 Session 11, [ADR 0122](../adr/0122-supervisor-team-task-oversight-view-read-only-composition.md))

`components/TeamTaskList.tsx` (route `/team/`, own tab in `Shell.tsx`) — a SEPARATE, read-only view
alongside `TaskList.tsx`, not a mode of it (ADR 0041 explicitly keeps `TaskList` a flat, instance-agnostic
list for the current user). Answers "what is my team working on?" rather than "what can I complete?".

- **No new backend endpoint** — composes two already-existing reads client-side: `GET
  /supervisor-assignments?supervisor_principal_id=<user.sub>` (`permission-service`, P31-S9's org-hierarchy
  foundation) for direct reports, and `GET /tasks` (`workflow-service`, already enriched with `claimed_by`
  since P31-S10) for the system-wide open-task list, filtered client-side to tasks whose `claimed_by`
  matches a direct report's `principal_id`.
- **`user.sub`, not `user.username`**, for the direct-reports lookup — the real Keycloak subject the
  gateway injects as `X-DMS-Principal`, the same identity that must already appear in
  `SupervisorAssignment.supervisor_principal_id` for P31-S10's org-hierarchy access grant to be exercisable
  at all (see ADR 0122 "Rationale" for the full reasoning).
- **Two distinct empty states**: no direct reports configured at all, vs. direct reports exist but
  currently have no attributable tasks — deliberately different messages, since they mean different things
  for an admin/supervisor trying to understand why the view is empty.

### Reassignment and unclaimed team-work visibility (Post-Roadmap Phase 35 Session 3, [ADR 0145](../adr/0145-task-claim-reassignment-expiry-notice-and-unclaimed-team-work-attribution.md))

Narrows (not reverses) the "purely read-only" framing above — the scope limit "only claimed tasks are
attributable" from P31-S11 is lifted for one specific case:

- **An unclaimed task is now also included when its process instance's `created_by` is a direct report** —
  `GET /tasks` already returned `created_by`, previously unused here. This is the only attribution signal
  an unclaimed task has; there is no BPMN lane/role-membership resolution (deliberately deferred, see ADR
  0145's Rationale — the user was asked via `AskUserQuestion` and chose instance-creator attribution over
  building that separate capability).
- **Assign/reassign actions**: each row's action column now shows "Zuweisen" (unclaimed) or "Neu zuweisen"
  (claimed), opening an inline form (same idiom as `TaskList.tsx`'s existing claim/grant forms — a `<tr>`
  rendered conditionally below the task's own row, `React.Fragment` with an explicit `key` since the
  shorthand `<>` fragment doesn't support one). Assigning calls the existing `claimTask`; reassigning calls
  the new `reassignTask` (`POST .../reassign`). Task **completion remains excluded** from this view — the
  deliberate line ADR 0145 draws between "oversight and assign/reassign" vs. "acting on someone else's
  work," which stays `TaskList.tsx`'s/the on-behalf-of delegation's territory.
- **Status column** replaces the old fixed "claimed by" wording with `t("teamTaskList.statusClaimed"
  |"statusUnclaimed")`, since a row can now be in either state.
- **Deliberately no actions here** (no claim/complete/grant buttons) — a manager viewing a report's work is
  not the same as acting on it; both existing "act for someone else" mechanisms (self-service delegation,
  ADR 0048; the org-hierarchy grant, ADR 0121) already live on `TaskList.tsx` itself. A "Vorgang öffnen"
  button per row reuses the same `?instance=` direct-link scheme (ADR 0109) as `TaskList.tsx`, via a real
  route navigation back to `/` rather than duplicating `InstanceDetail`'s rendering logic on `/team/`.

## Case Browsing (14.2, Post-Roadmap Phase 74 Session 1, ADR 0141)

`components/CasesPane.tsx` (route `/cases/`, own tab in `Shell.tsx`) — closes ADR 0141's own named gap:
that session built `user-ui`'s `CasesPane.tsx` and explicitly deferred "a real `case-service` browsing UI
in `reviewer-ui`," re-confirmed still open at Phase 65+'s gap-analysis round. Motivating problem: a
reviewer working a case-bound task (`business_key` = a `case-service` `Case` id) previously had no
case-context view of their own, only the generic task list showing the raw `business_key` as an opaque
string (`InstanceDetail.tsx`'s `<dd>{instance.business_key ?? "-"}</dd>`, unchanged by this session).

**Deliberately a smaller cut than `user-ui`'s own `CasesPane.tsx`, same list→detail shape** (list with a
status filter, clicking a row opens the detail view — same component name in both apps, deliberately
duplicated per ADR 0006, not shared):

- **No favorites** — this app has no `favorite-service` integration of any kind, unlike `user-ui`.
- **No XDOMEA/XJustiz export/import** — those are `archival.write`-gated power features (ADR 0126/0128/
  0129/0139); `Shell.tsx`'s own docstring already frames this app as "lean, approval-focused," and no
  motivating need for archival actions from within a reviewer's workflow was identified.
- **Document titles ARE resolved** (`getCaseDocument()`, a new, deliberately minimal
  `CaseDocumentSummary` type — just `id`/`title`/`current_version_number`, not the full `DocumentSummary`
  shape `user-ui` needs elsewhere) — and, going further than `user-ui`'s own `CasesPane.tsx`, **each
  resolved title is a working download link** (`downloadDocumentVersion()` + the same
  `triggerBrowserDownload()` blob-URL idiom used throughout this project's frontends). This app has no
  document preview/workspace infrastructure at all (confirmed via grep before building — no
  `getDocument`/tab-based viewer anywhere in `apps/reviewer-ui`), so "open the document" here means
  "download it," not opening an in-app preview tab the way `user-ui`'s `onOpenDocument` callback does.
- **A deleted document reference** renders the same non-clickable "Dokument gelöscht" placeholder as
  `user-ui`'s pane, same reasoning.

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
| Read/write theme and locale preference | `GET/PUT /api/auth-service/me/preferences` (`locale` since Phase 47 Session 2) |
| Emergency shutdown / maintenance mode status | `GET /api/permission-service/maintenance-mode` |

## Theming/i18n/Auth State

Identical provider copy from user-ui/admin-ui/process-designer (`ThemeProvider`, `I18nProvider`, `auth-context.tsx`), own `src/i18n/de.json`, global `dms.tokens` storage key (single installation like process-designer/user-ui, no `InstallationSwitcher` like admin-ui).

**Since Phase 47 Session 2** ([ADR 0167](../adr/0167-locale-switcher-pattern-and-office-addin-host-locale.md)): the language-switcher pattern proven in `process-designer` (P47-S1) propagated here — own `src/i18n/en.json`, `I18nProvider`'s previously-static `locale` prop replaced by a new `LocaleProvider`/`useLocale()` (`lib/locale-context.tsx`, identical cross-device persistence and hydration-safety design), a new `LocaleSwitcher.tsx` wired into `Shell.tsx`'s top bar next to the already-working `ThemeSwitcher`.

**Since Post-Roadmap Phase 33 Session 1** ([ADR 0135](../adr/0135-accessibility-audit-five-frontend-apps.md)): the `high-contrast` theme's `--dms-accent-bg` was fixed from an accidental `#ffff00` (identical to `--dms-accent`, rendering `.badge-pending` — the signature badge in `TaskList.tsx`/`InstanceDetail.tsx` — as illegible yellow-on-yellow) to `#000000`, matching `--dms-danger-bg`/`--dms-success-bg`'s pattern; `.badge` also gained the `high-contrast`-only `border: 1px solid currentColor` rule `user-ui`/`admin-ui` already had since ADR 0119, so `.badge-approved`/`.badge-rejected` keep a visible pill shape once the background flattens to the page background.

**Since Phase 49 Session 1** ([ADR 0168](../adr/0168-shared-design-tokens-and-scales.md)): `globals.css`'s own `--dms-*` color-token declarations (this app already had the ADR 0135 fix, unlike `user-ui`/`admin-ui`/`process-designer`) were removed and replaced by `@import "../../../../libs/dms-ui/tokens.css";` — the shared, de-drifted token source built in Phase 48 Session 1. Visually unchanged (confirmed live via Playwright, both light and high-contrast). Component-level hardcoded spacing/radius/font-size values throughout the rest of `globals.css` were also switched to the new `--dms-space-*`/`--dms-radius-*`/`--dms-font-size-*` scale tokens where they matched a scale step.

**Since Post-Roadmap Phase 75 Session 1** ([ADR 0218](../adr/0218-tailwind-css-v4-tooling-foundation.md)): `tailwindcss`/`@tailwindcss/postcss`/`postcss` added as devDependencies, a new `postcss.config.mjs`, and `libs/dms-ui/tailwind-preset.css` imported into `globals.css` (mapping Tailwind utility classes onto the `--dms-*` tokens above) — tooling only, this app's own UI is unchanged (preflight deliberately omitted for now, see the ADR); the actual redesign starts at P75-S2.

**Since Post-Roadmap Phase 75 Session 2** ([ADR 0219](../adr/0219-login-page-tailwind-redesign-accent-fg-token-cascade-fix.md)): `/login` rebuilt as a centered card (`rounded-lg border bg-surface shadow-lg`) with labeled inputs, visible focus rings, and an error alert, replacing the old bare `<main>`/`<form>` layout. Uses the new `--dms-accent-fg`/`--color-accent-fg` token (added this session, see the ADR) on the primary button instead of a hardcoded `text-white`, after a WCAG contrast failure was found live in the dark and high-contrast themes. Live-verified in all three themes plus the error/focus states, and against the real running Docker container.

**Correction (Post-Roadmap Phase 75 Session 3, [ADR 0220](../adr/0220-office-addin-tailwind-rollout-primary-button-border-fix.md))**: the login submit button was silently relying on the browser's default unstyled `<button>` border (invisible in a screenshot, found via `getComputedStyle()` while verifying `office-addin`'s rollout) — fixed with an explicit `border-0`.

**Correction (Post-Roadmap Phase 75 Session 3 continuation, [ADR 0221](../adr/0221-migration-console-tailwind-rollout-form-font-inherit-fix.md))**: login inputs were rendering in `Arial` instead of this app's own font stack (no global `input,select,button,textarea{font:inherit}` reset existed) — fixed with a small `@layer base` addition to `globals.css`. This is a surgical fix only; this app's own full Tailwind rollout beyond the login page is still queued as part of the small-apps group, not attempted here.

## Build & Delivery

Two-stage Docker image (`apps/reviewer-ui/Dockerfile`, `node:22-alpine` build stage → `nginx:alpine` runtime), `NEXT_PUBLIC_GATEWAY_BASE_URL` as a build arg, overridable via `REVIEWER_UI_GATEWAY_BASE_URL` in `infra/.env`. `infra/docker-compose.yml`: port `${REVIEWER_UI_PORT:-3005}:80` — **not** 3003 (already taken by `GRAFANA_PORT`, 10.1, see ADR 0041).

**Since Phase 49 Session 1**: the build stage's `WORKDIR`/`COPY` layout changed to mirror the actual repo shape (`/repo/apps/reviewer-ui/`, plus `/repo/libs/dms-ui/`) instead of flattening this app into `/app` — needed so `globals.css`'s new relative `@import` of `libs/dms-ui/tokens.css` resolves identically to a local `npm run build`.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build` — TypeScript check, ESLint, production-ready static export.
- `npm test` (Vitest + Testing Library, **55 tests since Post-Roadmap Phase 74 Session 1**, previously 50
  actually present (50 = 55 minus this session's own +5, counted directly from the real `npm test` run
  before writing this note — drifted from this doc's own last-recorded "47," not investigated further,
  same running-count-drift pattern already seen and accepted elsewhere in this project's docs) — new
  `cases-pane.test.tsx` (empty state, list→detail navigation
  incl. real `getCase`/`listCaseDocuments` calls, the back button, the deleted-document non-clickable
  placeholder skipping title resolution entirely, resolving a document's title and downloading it via
  `downloadDocumentVersion`); real Playwright E2E `e2e/cases.spec.ts` added, browsing the real case list
  in the shared dev stack and opening a case's detail view (same "not empty in practice" situation as
  `tasks.spec.ts`'s task inbox). **Live-verified in a real (headed) browser** against the rebuilt running
  stack, going beyond the E2E spec itself: a real case and a real case-document reference created via the
  API, case list/detail rendered correctly (screenshot), the document title resolved to its real title
  (not the raw UUID), and clicking it triggered a real, successful file download (`Anschreiben
  P74S1.txt`) confirmed via Playwright's own download-event API. Before Phase 74 Session 1, **47 tests
  since Phase 47 Session 2**, previously 44 — adds `locale-context.test.tsx` (default/cache-after-mount/persistence, 3 tests, same hydration-safety design as `process-designer`'s) and fixes `require-auth.test.tsx` to wrap `RequireAuth` in the new `LocaleProvider` instead of the now-internal `I18nProvider` directly (that test renders `Shell.tsx`, which now also renders `LocaleSwitcher`); before that, **44 tests**, previously 41 — since **Post-Roadmap Phase 35 Session 3** ([ADR 0145](../adr/0145-task-claim-reassignment-expiry-notice-and-unclaimed-team-work-attribution.md)): `team-task-list.test.tsx` grew from 4 to 7 tests — a new fixture distinguishing an unclaimed task belonging to a stranger's instance (still excluded) from one belonging to a direct report's instance (now included), plus two new interaction tests (assigning an unclaimed team task, reassigning a claimed one); before that, **41 tests**, previously 37 — since **Post-Roadmap Phase 31 Session 11** ([ADR 0122](../adr/0122-supervisor-team-task-oversight-view-read-only-composition.md)): new `team-task-list.test.tsx` (4 tests: dedicated empty state with no direct reports at all, empty state with direct reports but no open claimed tasks, lists only a direct report's claimed task while excluding an unclaimed one and one claimed by a non-report, opens the Vorgang for a listed team task); before that, **37 tests**, previously 20 — since **Post-Roadmap Phase 31 Session 10** ([ADR 0121](../adr/0121-dynamic-org-hierarchy-access-grants-task-claim-and-delegation-reuse.md)): `TaskList` gained 6 tests (claiming an unclaimed task as the logged-in user, a claim by someone else shows no release button, releasing an own claim, the grant form appears only once claimed and reports the resolved deputies including the graceful-empty case, the grant form stays hidden for an unclaimed task, `org_unit_of` is only sent for `grant_kind="org_unit"`), `instance-detail.test.tsx` unchanged (the new "claimed by" column there is read-only display, no new interaction to test); before that, **20 tests**, previously 18 — since **Post-Roadmap Phase 22 Session 4**: `ApprovalList` now has 8 tests instead of 6, the previous `window.prompt` test replaced by three: opening the inline form + rejecting with a justification, rejecting without a justification (empty field → `reason: undefined`), canceling closes the form without an API call): `AuthProvider` (login/logout/session restoration, 4 tests), `TaskList` (empty list, listing with process/related-object context, signature badge + required field, successful completion incl. reload, rejection on invalid JSON in the additional data, since **P14-S11** additionally: no "On behalf of" selector without active delegations, the selector appears populated with at least one active delegation, completing "on behalf of" also sends `onBehalfOfPrincipalId`, 8 tests), `ApprovalList` (default filter "open", empty list, expanding the detail payload, approving as the logged-in user, rejecting with an optional justification via the new inline form, rejecting without a justification, canceling the form, no actions on an already-decided request, 8 tests).
- Verified live against the built container in a real (headless) browser (login, task list incl. edit form, approvals tab incl. status-filter switching, theme switcher to dark — each without console errors; the task list showed real tasks left over from earlier test runs, additional evidence that `GET /tasks` correctly aggregates real data).

## Open Points

- **No server push/no notifications** — a pure pull interface, must be actively reloaded (see ADR 0041 "Consequences").
- **No lane-/role-based pre-selection of the task list** ("only my tasks") — `workflow-service` does not yet enforce BPMN lanes anywhere (an already-documented limitation, `docs/services/workflow-service.md` "Open Points"), every logged-in principal sees the same complete list.
- ~~No own rejection dialog form — `window.prompt` for the optional justification~~ — **fixed in Post-Roadmap Phase 22 Session 4**: an inline form directly in the table row replaces the native dialog, see "Approval Inbox" above.
