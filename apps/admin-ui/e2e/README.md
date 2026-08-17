# End-to-end tests (Playwright)

Real-browser tests exercising key admin flows against the already-running
`infra/docker-compose.yml` stack - login through the actual gateway, real
user/object-type CRUD, a real registry read. Separate from `tests/` (Vitest
component tests, jsdom, mocked network) - both keep running independently.

Same pattern as `apps/user-ui/e2e/` (read that directory's README first for
the full "why" behind every gotcha below - this file only notes what's
admin-ui-specific).

## Running

No Node.js on the host is required or expected - these tests run inside the
same `mcr.microsoft.com/playwright:v1.48.0-jammy` image used elsewhere in
this project, with `--network host` so it can reach the stack's published
ports directly:

```bash
# from the repo root
docker run --rm --network host -v "$(pwd):/repo" -w /repo/apps/admin-ui \
  mcr.microsoft.com/playwright:v1.48.0-jammy \
  bash -c "npm install && npx playwright test"
```

Useful variations:

```bash
# One file / one test
... npx playwright test e2e/user-management.spec.ts
... npx playwright test -g "creates an object type"
```

## Prerequisites

The docker-compose stack (`infra/`) must already be running, including a
bootstrapped `users-admin` technical account (present by default, realm
role `domain-admin-users` -> `admin.user_management`). No dev server is
started by `playwright.config.ts` - `admin-ui` is served as a static export
via its own nginx container (`http://localhost:3001`), same as production.

**No new permission grant needed** (unlike user-ui's `document.read`/
`folder.read` gap): verified directly against the running stack before
writing these tests that `users-admin`'s existing role covers everything
exercised here - creating/deleting a Keycloak user via auth-service, and
creating/deleting an object type via object-type-service both succeed with
no extra role assignment.

**`nginx.conf`'s `absolute_redirect off;`**: the source file already has it
(same as all 6 apps), but if the running `admin-ui` container was built
*before* that line was added to the file, it's serving a stale config
without it - every login (`/login` -> `/login/`) then fails with
`net::ERR_CONNECTION_REFUSED` in a real browser (the absolute `Location:
http://localhost/login/` redirect, missing the mapped host port, that a
headless browser follows and a plain `curl`/`fetch` never notices - see
user-ui's README for the full mechanism). Check what the *running*
container actually serves, not just the source file, before assuming this
is fixed:

```bash
docker exec dms-admin-ui-1 cat /etc/nginx/conf.d/default.conf | grep absolute_redirect
# if empty/missing:
cd infra && docker compose up -d --build admin-ui
```

## Design notes

- **Reused technical account, no isolated-folder-style fixture**: unlike
  user-ui's document/folder flows, none of the admin flows here need a
  scratch workspace to operate inside - each test creates its own
  uniquely-named row (a user, an object type) and deletes it again at the
  end of the same test, which doubles as the assertion that delete works.
  No separate teardown fixture.
- **Selectors use the live German UI text** (`src/i18n/de.json`), scoped to
  the relevant `<form aria-label="...">` or table `row` to disambiguate
  reused strings (e.g. "Anlegen"/"Löschen"/"Speichern" appear on several
  forms on the same page - `object-types` renders both `ObjectTypeEditor`
  and `LayoutDesigner` on one page).
- **Coverage**: login (happy path + wrong credentials), user management
  CRUD (`/users/` - create, verify in list, delete), object-type editor
  CRUD (`/object-types/` - create with an attribute, edit to add a second
  attribute, delete), and registry overview (`/registry/` - asserts the
  table is actually populated with real service-registry rows and that
  refresh re-fetches, not just a page-load smoke test).
- Same version pinning / `overrides` / `e2e/tsconfig.json` /
  `eslint.config.mjs` `e2e/**` override rationale as user-ui - copied
  verbatim, see that project's README for the details.
