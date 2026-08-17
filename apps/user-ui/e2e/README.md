# End-to-end tests (Playwright)

Real-browser tests exercising key user flows against the already-running
`infra/docker-compose.yml` stack - login through the actual gateway, real
folder/document operations, real search. Separate from `tests/` (Vitest
component tests, jsdom, mocked network) - both keep running independently.

## Running

No Node.js on the host is required or expected - these tests run inside the
same `mcr.microsoft.com/playwright:v1.48.0-jammy` image already used
elsewhere in this project for one-off manual browser verification
(`docs/services/*.md`), just with `--network host` so it can reach the
stack's published ports directly:

```bash
# from the repo root
docker run --rm --network host -v "$(pwd):/repo" -w /repo/apps/user-ui \
  mcr.microsoft.com/playwright:v1.48.0-jammy \
  bash -c "npm install && npx playwright test"
```

Useful variations:

```bash
# One file / one test
... npx playwright test e2e/login.spec.ts
... npx playwright test -g "creates a subfolder"

# See what happened on failure (HTML report, trace viewer)
... npx playwright show-trace test-results/.../trace.zip
```

## Prerequisites

The docker-compose stack (`infra/`) must already be running, including a
bootstrapped `users-admin` technical account (present by default). No dev
server is started by `playwright.config.ts` - `user-ui` is served as a
static export via its own nginx container, same as production.

**One-time permission grant** (not part of the app or its tests - a
Postgres row in the local dev stack, nothing to commit): `users-admin` has
no `document.read`/`folder.read` grant by default (these are gated
permissions, not part of the "everyone" default role - discovered while
building this suite, see the git history of this file's introducing commit
for the full story). Without it, uploads/folder-browsing still work
(document-service/folder-service don't gate reads), but **search returns
zero results for everyone**, always, regardless of query - `search-service`
filters results through a `document.read` permission check that facet
counts don't go through, so facets look populated while results stay
empty. Grant once per stack:

```bash
TOKEN=$(curl -s http://localhost:8009/api/auth-service/login -X POST \
  -H "Content-Type: application/json" \
  -d '{"username":"users-admin","password":"users-admin"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
ROLE_ID=$(curl -s -X POST http://localhost:8009/api/permission-service/roles \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"e2e-playwright-reader","description":"document/folder read+write for the E2E Playwright account","permissions":["document.read","document.write","folder.read","folder.write"]}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['id'])")
curl -s -X POST http://localhost:8009/api/permission-service/role-assignments \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"principal_type\":\"user\",\"principal_id\":\"2\",\"role_id\":$ROLE_ID,\"resource_id\":\"root\"}"
```

(`principal_id: "2"` is `users-admin`'s `sub` claim in this stack - confirm
via `GET /api/auth-service/me` if it ever differs.) Check first whether a
role named `e2e-playwright-reader` already exists
(`GET /permission-service/roles`) before creating another - the project's
existing `search-service`/`share-link` test suites create a **new** role
per test run without ever cleaning them up (hundreds of `search-test-role-*`
rows accumulated in this dev stack) - don't repeat that pattern here.

## Design notes

- **Isolated test data** (`fixtures.ts`): every test that creates data works
  inside a freshly created, uniquely-named root folder, cascading-trashed
  in an automatic teardown (`testWithIsolatedFolder`) - same rationale as
  `loadtest/k6/scenario.js`'s `setup()`/`teardown()`: never depend on or
  pollute whatever else already exists in the shared dev stack's root
  folder (which accumulates a large number of pre-existing entries over
  time).
- **Selectors use the live German UI text**, not `data-testid` attributes -
  `de.json` is this project's real, active locale (not just a translation
  target), and the existing codebase already exposes good `aria-label`/
  `id`/`placeholder` coverage on interactive elements. Scope broad text
  matches (e.g. "Hochladen" appears on both the upload-toggle button and
  the upload form's submit button) to a specific container/role rather than
  adding test-only markup.
- **Version pinning**: `@playwright/test` is pinned to the exact version
  baked into `mcr.microsoft.com/playwright:v1.48.0-jammy`'s browser
  binaries (`1.48.0`, not `^1.48.0`) - a caret range resolves to whatever
  is newest on install, which then can't find a matching browser executable
  inside the pinned image (`browserType.launch: Executable doesn't exist`).
  If the docker image tag above is ever bumped, bump this version to match
  in the same change. Next.js separately declares an *optional* peer
  dependency on a newer `@playwright/test` (`^1.51.1`) than the one pinned
  here, which `npm install` would otherwise refuse to resolve - fixed via
  `"overrides": { "@playwright/test": "1.48.0" }` in `package.json`, not
  `--legacy-peer-deps`. That flag looks like the obvious fix (it silences
  the same error) but disables npm's automatic peer-dependency
  installation *entirely*, including for `@testing-library/dom` - an
  actual peer dependency of `@testing-library/react` that every existing
  Vitest test relies on transitively. With `--legacy-peer-deps`, it
  silently never gets installed and `tsc` fails across the whole `tests/`
  suite with "no exported member 'screen'/'waitFor'/'fireEvent'" - a
  genuinely nasty one to track down since the failure is nowhere near
  the actual cause. `overrides` resolves the one real conflict without
  touching how peers are installed everywhere else.
- **`e2e/tsconfig.json`**: a separate, minimal tsconfig for this directory
  (excluded from the root `tsconfig.json`, which drives `npm run
  typecheck`) - Playwright's own globals and the Vitest/jsdom globals used
  by `tests/` don't need to coexist in one compilation unit. Run
  `npx tsc --project e2e/tsconfig.json --noEmit` to typecheck this
  directory on its own.
- **`eslint.config.mjs`** disables `react-hooks/rules-of-hooks` for `e2e/**`
  - Playwright's fixture API conventionally names its callback parameter
  `use` (`base.extend({ x: async ({}, use) => { ... } })`), which
  `react-hooks` mistakes for the React 19 `use()` hook based on the name
  alone. `e2e/` isn't React code.
- **`nginx.conf`'s `absolute_redirect off;`** (all 6 apps, not just this
  one): nginx's automatic trailing-slash redirect (`/login` → `/login/`)
  otherwise emits an absolute `Location` header built from `listen`/
  `server_name` (i.e. port 80, the container-internal port) - correct
  inside the container, wrong for a client reaching the app through any of
  this stack's mapped host ports. A real browser (headless or not) follows
  that redirect and fails outright; `curl`/`fetch` without a redirect flag
  never notices, since neither follows redirects by default. This was a
  real, user-facing bug independent of Playwright, not a test-only fix.
