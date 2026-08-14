# migration-console

**Responsibility:** Standalone frontend application for transfer operations against `migration-service` (7.2/8, literally: "a migration console for transfer operations"), P14-S2. Two areas: managing installation pairing and starting/observing transfers.
**Concept reference:** 8, 7.2
**No own Postgres schema** — pure client-side rendered SPA (static export, same pattern as `apps/user-ui`, see [ADR 0006](../adr/0006-user-ui-static-export-spa.md)), no own backend process.
**ADR:** [0041 — Scope + new cross-instance task list, no new authorization layer](../adr/0041-reviewer-ui-migration-console-scope-and-cross-instance-tasks.md)

## Location in the repo

`apps/migration-console/` — deliberately **not** under `services/` (Node/React toolchain instead of the Python service template, ADR 0006) and deliberately **not** part of the Admin UI (literal concept requirement: "standalone ... migration console").

## Pages

| Route | Purpose |
|---|---|
| `/login/` | Login (identical to user-ui/admin-ui/process-designer/reviewer-ui) |
| `/` | `TransferConsole` — transfer overview/start, only reachable with a valid session |
| `/paired-installations/` | `PairedInstallationList` — installation pairing |

`RequireAuth` renders a shared `Shell` (header with tab navigation, theme switcher, logout) as well as, before that, a `MaintenanceBanner` (emergency shutdown, 4.8).

## Paired installations (7.2, `components/PairedInstallationList.tsx`)

Direct installation pairing instead of hub mediation ([ADR 0034](../adr/0034-migration-service-direct-pairing-and-generic-connector-service-tasks.md)) — create (`POST /paired-installations`, an empty `api_key` lets `migration-service` generate a new one), list, remove. The generated API key is **shown only once, immediately after creation** (never retrievable again via `GET`, identical principle to `federation-hub-service`) — the console displays it directly in the form area as a success message, not in the table.

## Transfers (7.2, `components/TransferConsole.tsx`)

The start form (source folder ID, target installation from the pairing list, dry-run toggle, optional deletion period in days) calls `POST /transfers`. Two possible outcomes:

- **`status: "started"`** — the transfer starts directly, appearing immediately in the list.
- **`status: "pending_approval"`** — four-eyes principle (4.3) is actively configured for `migration.transfer.start` (`permission-service`'s `approval-config`); the console shows a notice with the generated `approval_request_id` — the actual approval/rejection runs via the generic approval inbox in `reviewer-ui`, not via this console itself (no duplicate approval UI).

The transfer list shows a status badge (green for `released`/`deleted`/`dry_run_completed`, red for `failed`, otherwise neutral "in progress"), document progress (`documents_copied`/`documents_total`/`documents_verified`), and an expandable detail row with the error message (if `failed`) as well as the complete phase timeline (`locked_at`/`copied_at`/`verified_at`/`released_at`/`deletion_scheduled_at`/`deleted_at`). **Lightweight polling every 5 seconds** (`setInterval`, same pattern as `MaintenanceBanner`'s 30s poll) — a transfer continues to run in the background as an asynchronous `workflow-service` instance; without a re-fetch the console would remain stuck at the state of the last call.

**Deliberately not represented in the console**: the `.../steps/{lock|copy|verify|release|delete-source|dry-run-check}` endpoints (internal targets of automatic `connector_call` service tasks, 7.1/P12-S2) and the entire `/inbound/*` API (called by the paired counterpart, never by local operating staff) — both are pure workflow/protocol mechanics without an operator interface.

## Authorization

**No capability-gated actions in this app** — `migration-service` gates its write endpoints exclusively via its own license check (`license_gate`, Concept 9.3: demo mode blocks `POST /transfers`/`POST /paired-installations`, not reading), no domain-separated admin role. `RequireAuth` only checks whether a valid session exists at all. An unlicensed installation or one in demo mode shows the corresponding `403` error message directly in the respective form (no dedicated license status banner as in the Admin UI, not added for scope reasons in this session).

## Connection to the backend

Exclusively via the API gateway (3.5):

| Action | Gateway call |
|---|---|
| Login | `POST /api/auth-service/login` |
| Identity after login | `GET /api/auth-service/me` |
| List/create/remove paired installations | `GET/POST/DELETE /api/migration-service/paired-installations[/{id}]` |
| List/create/detail transfers | `GET/POST /api/migration-service/transfers`, `GET /api/migration-service/transfers/{id}` |
| Read/write theme preference | `GET/PUT /api/auth-service/me/preferences` |
| Emergency shutdown / maintenance mode status | `GET /api/permission-service/maintenance-mode` |

## Theming/i18n/auth state

Identical provider copy from user-ui/admin-ui/process-designer/reviewer-ui (`ThemeProvider`, `I18nProvider`, `auth-context.tsx`), own `src/i18n/de.json`, global `dms.tokens` storage key (single installation).

## Build & deployment

Two-stage Docker image (`apps/migration-console/Dockerfile`, `node:22-alpine` build stage → `nginx:alpine` runtime), `NEXT_PUBLIC_GATEWAY_BASE_URL` as a build arg, overridable via `MIGRATION_CONSOLE_GATEWAY_BASE_URL` in `infra/.env`. `infra/docker-compose.yml`: port `${MIGRATION_CONSOLE_PORT:-3004}:80`.

## Tests

- `npm run typecheck` / `npm run lint` / `npm run build` — TypeScript check, ESLint, production-ready static export.
- `npm test` (Vitest + Testing Library, **14 tests**): `AuthProvider` (4 tests, identical to the pattern of the other apps), `PairedInstallationList` (empty list, listing, creation incl. one-time API key display, deletion after confirmation, 4 tests), `TransferConsole` (empty list, listing with resolved target installation name + progress, expanding the detail timeline, starting a transfer incl. reload, four-eyes notice on `pending_approval`, polling interval triggers a re-fetch, 6 tests).
- Live verified against the built container in a real (headless) browser (login, transfer list incl. expanding the detail timeline, installation pairing incl. the full create→show-one-time-key→delete cycle, theme switch to dark — each without console errors; the lists showed real installations/transfers left over from earlier test runs, including the self-loopback test from P12-S2).

## Open Points

- **No server push** — pure polling every 5s instead of WebSocket/SSE (see ADR 0041 "Consequences").
- **No own license status banner** — unlike the Admin UI, this console does not proactively display the `migration-service` license status, only reactively as an error message on a blocked write attempt.
- **Approval of `pending_approval` transfers runs exclusively via `reviewer-ui`** — deliberately no own, second approval UI in this console (see "Transfers" above).
- Inherits the same already documented limitations of `migration-service` itself (see `docs/services/migration-service.md` "Deliberate limitations"): no target folder selection dialog (target folder always lands at the root of the target installation), no historical timestamps on copied document versions.
