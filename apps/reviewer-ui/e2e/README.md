# End-to-end tests (Playwright)

Real-browser tests exercising key user flows against the already-running
`infra/docker-compose.yml` stack - login through the actual gateway, the
task inbox, and the approval inbox. Separate from `tests/` (Vitest
component tests, jsdom, mocked network) - both keep running independently.

Same setup and same hard-won fixes as `apps/user-ui/e2e/` (read that
README for the full "why" behind each one) - `nginx.conf`'s
`absolute_redirect off;`, `@playwright/test` pinned to `1.48.0` with an
`overrides` entry (not `--legacy-peer-deps`), a separate `e2e/tsconfig.json`,
and an `eslint.config.mjs` override disabling `react-hooks/rules-of-hooks`
for `e2e/**`.

## Running

```bash
# from the repo root
docker run --rm --network host -v "$(pwd):/repo" -w /repo/apps/reviewer-ui \
  mcr.microsoft.com/playwright:v1.48.0-jammy \
  bash -c "npm install && npx playwright test"
```

```bash
# One file / one test
... npx playwright test e2e/login.spec.ts
... npx playwright test -g "status filter"
```

## Prerequisites

The docker-compose stack (`infra/`) must already be running, including a
bootstrapped `users-admin` technical account. No dev server is started by
`playwright.config.ts` - `reviewer-ui` is served as a static export via its
own nginx container, same as production.

**No permission grant needed**: per `docs/services/reviewer-ui.md`
"Authorization", neither task completion nor approval decisions are bound
server-side to a domain-separated admin role - `RequireAuth` only checks
whether a valid session exists. `users-admin` works out of the box.

## Design notes

- **`tasks.spec.ts` reads real, unpredictable data** - `GET /tasks`
  aggregates ready tasks across every running instance in the shared dev
  stack, which in practice is *not* empty (other suites leave real, orphaned
  process instances behind). The test opens the "Bearbeiten" edit form on
  whatever the first row is and drives its client-side JSON validation path
  (invalid JSON never reaches the backend) plus cancel - it deliberately
  never completes a task, since these rows belong to process instances this
  suite didn't create and isn't in a position to judge safe to finish. Falls
  back to asserting the empty state if the inbox happens to be empty.
- **`approvals.spec.ts` stays smoke-level** - checked
  `GET /api/permission-service/approval-config` directly: nothing currently
  requires approval in this dev stack (`migration.transfer.start` and every
  other real action type there has `requires_approval: false`), so the
  inbox is genuinely empty. Per the task scope, deliberately not flipping
  `approval-config` just to populate this list on a shared stack. The test
  instead exercises the status-filter dropdown across every option
  (open/approved/rejected/all), asserting no error banner appears.
- Selectors use the live German UI text (`de.json`), same rationale as
  user-ui/e2e.
