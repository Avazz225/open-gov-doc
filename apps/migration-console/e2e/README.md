# End-to-end tests (Playwright)

Real-browser tests exercising key user flows against the already-running
`infra/docker-compose.yml` stack - login through the actual gateway, real
installation pairing. Separate from `tests/` (Vitest component tests,
jsdom, mocked network) - both keep running independently.

Same setup and same hard-won fixes as `apps/user-ui/e2e/` (read that
README for the full "why" behind each one) - `nginx.conf`'s
`absolute_redirect off;`, `@playwright/test` pinned to `1.48.0` with an
`overrides` entry (not `--legacy-peer-deps`), a separate `e2e/tsconfig.json`,
and an `eslint.config.mjs` override disabling `react-hooks/rules-of-hooks`
for `e2e/**`.

## Running

```bash
# from the repo root
docker run --rm --network host -v "$(pwd):/repo" -w /repo/apps/migration-console \
  mcr.microsoft.com/playwright:v1.48.0-jammy \
  bash -c "npm install && npx playwright test"
```

```bash
# One file / one test
... npx playwright test e2e/login.spec.ts
... npx playwright test -g "creates a paired installation"
```

## Prerequisites

The docker-compose stack (`infra/`) must already be running, including a
bootstrapped `users-admin` technical account. No dev server is started by
`playwright.config.ts` - `migration-console` is served as a static export
via its own nginx container, same as production.

**No permission grant needed**: per `docs/services/migration-console.md`
"Authorization", `migration-service` gates its write endpoints only via its
own license check (`license_gate`), not a domain-separated admin role -
`RequireAuth` only checks whether a valid session exists. `users-admin`
works out of the box.

## Design notes

- **`paired-installations.spec.ts`** runs the full create -> show
  one-time-key -> list -> delete cycle through the real UI, and its
  `pairedInstallationName` fixture (`fixtures.ts`) force-deletes the row via
  a direct API call in teardown regardless of whether the UI's own delete
  flow ran or succeeded - the shared dev stack's paired-installations list
  already has hundreds of leftover rows from other test suites (self-loopback
  pairings, etc. from `migration-service`'s own test history); this suite
  doesn't add to that pile.
- **`transfers.spec.ts` stays smoke-level** - starting a transfer end-to-end
  needs a genuine paired counterpart instance to progress through its phases
  (lock/copy/verify/release), out of reach for an isolated E2E run against a
  single stack. The test instead loads the console (whatever transfers are
  already present, if any) and exercises the status-filter dropdown across
  every option, asserting no error banner appears.
- Selectors use the live German UI text (`de.json`), same rationale as
  user-ui/e2e.
