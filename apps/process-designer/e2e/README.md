# End-to-end tests (Playwright)

Real-browser tests exercising key user flows against the already-running
`infra/docker-compose.yml` stack - login through the actual gateway, real
BPMN canvas interaction, real process-definition creation via
`workflow-service`. Separate from `tests/` (Vitest component tests, jsdom,
mocked `bpmn-js`) - both keep running independently. Same pattern as
`apps/user-ui/e2e/` (read that directory's README too - most of the
underlying gotchas are identical, project-wide fixes).

## Running

No Node.js on the host is required or expected - these tests run inside the
same `mcr.microsoft.com/playwright:v1.48.0-jammy` image already used
elsewhere in this project, just with `--network host` so it can reach the
stack's published ports directly:

```bash
# from the repo root
docker run --rm --network host -v "$(pwd):/repo" -w /repo/apps/process-designer \
  mcr.microsoft.com/playwright:v1.48.0-jammy \
  bash -c "npm install && npx playwright test"
```

Useful variations:

```bash
# One file / one test
... npx playwright test e2e/login.spec.ts
... npx playwright test -g "adds a task"

# See what happened on failure (HTML report, trace viewer)
... npx playwright show-trace test-results/.../trace.zip
```

## Prerequisites

The docker-compose stack (`infra/`) must already be running, including a
bootstrapped `users-admin` technical account (present by default).
`process-designer` is served as a static export via its own nginx
container, same as production - no dev server is started by
`playwright.config.ts`.

**Permission grant (automatic since P83-S8):** saving/deleting a process
or DMN definition requires the `admin.object_config` capability (see
`docs/services/process-designer.md` "Authorization"), which `users-admin`
does not hold by default. The auto fixture `objectConfigRole`
(`e2e/fixtures.ts`) grants the existing `domain-admin-config` role (looked up
by permission, never by id) to the test account for the length of each test
and revokes it afterwards - no manual one-time grant per stack any more,
and a stack reset no longer breaks the suite silently. (P83-S8 ran the suite
with `workers: 1` because of a race in permission-service's materialized cache;
P84-S1 fixed the race with a cache generation, ADR 0239, and the suite runs in
parallel again.)

## Design notes

- **Isolated test data**: `designer.spec.ts` creates its process definition
  under a name unique per run (`e2e-process-designer-${Date.now()}`) rather
  than pre-seeding an isolated container the way `apps/user-ui/e2e` uses an
  isolated root folder - there is no folder-equivalent scoping concept for
  process definitions, and the id needed for cleanup only exists *after*
  the UI creates it (the create flow through the UI is the thing under
  test, not a fixture precondition). Cleanup happens in `test.afterEach`
  via a direct `DELETE /workflow-service/process-definitions/{id}` API
  call using the id captured from the post-save redirect URL
  (`/designer/?id=<id>`) - a real hard delete (see
  `repository.py#delete_process_definition`: `session.delete(...)`, no
  soft-delete/tombstone column), safe here because the test never starts a
  process instance against its own definition (that's the one case the
  endpoint refuses with `409`). The dev stack's `process-definitions` list
  already carries ~380 pre-existing entries from other suites - the unique
  name is only there so the test can find its own row again, not to keep
  the list itself clean (that's what the `afterEach` delete is for).
- **bpmn-js canvas interaction** (`designer.spec.ts`): confirmed via a live
  DOM probe against the running app (not assumed from bpmn-js docs) that
  the palette (`.djs-palette .entry[data-action="create.task"]`, etc.) and
  placed elements (`.djs-shape[data-element-id="..."]`) are plain,
  Playwright-clickable SVG/HTML elements - `bpmn-js` uses a **click-tool,
  then click-canvas-to-place** interaction for its palette (not HTML5
  drag-and-drop): click the palette entry once, then a single
  `page.mouse.click(x, y)` on the canvas SVG places the new shape at that
  point. This is far more reliable through Playwright than reproducing a
  real drag gesture (`dragTo`/manual `mouse.down`+`move`+`up` sequences are
  notoriously flaky against custom, non-native-`<input type=file>`-style
  drag targets) and was verified to actually place a second `.djs-shape`
  (an auto-generated `Activity_*` id) alongside the starter diagram's
  `StartEvent_1`. The canvas SVG itself needs a specific selector
  (`.djs-container svg[data-element-id]`) - a bare `svg` under the canvas
  matches 3 elements (the diagram SVG, the palette's built-in search-icon
  SVG, and the "Powered by bpmn.io" badge SVG). The selector is scoped to
  bpmn-js's own `.djs-container`, not an app class: the Tailwind migration
  (P75-S4) removed `.designer-canvas`/`.designer-toolbar`, which left this
  suite stale until P83-S8; the name field is addressed by its id
  (`#process-name`) for the same reason.
- **Version pinning / `overrides` / `--legacy-peer-deps` trap / `e2e/tsconfig.json`
  / `eslint.config.mjs`'s `react-hooks/rules-of-hooks` override /
  `nginx.conf`'s `absolute_redirect off;`**: identical rationale to
  `apps/user-ui/e2e/README.md` - see there for the full "why" on each, not
  repeated here.
- **The running `process-designer` container had to be rebuilt before any
  of this could work at all**: `nginx.conf` on disk already had
  `absolute_redirect off;` (part of the project-wide fix applied to all 6
  apps), but the *running* `dms-process-designer-1` container predated that
  change and was never rebuilt - `docker exec dms-process-designer-1 cat
  /etc/nginx/conf.d/default.conf` showed the old config without the fix.
  Concretely this made every relative-URL navigation (`page.goto("/login")`,
  and `baseURL` + a relative path, which is exactly how Playwright
  navigates by default) fail with `net::ERR_CONNECTION_REFUSED` - not a
  timeout, an immediate refusal, because nginx's redirect for `/login` ->
  `/login/` emitted `Location: http://localhost/login/` (host without
  port, i.e. an implicit port 80) and Chromium followed it straight into a
  dead end (nothing external listens on port 80; the mapped host port is
  3002). Confirmed via `curl -sv http://localhost:3002/login` showing the
  bad `Location` header pre-rebuild and the correct relative
  `Location: /login/` post-rebuild. Fixed by `cd infra && docker compose up
  -d --build process-designer`. If this suite ever starts failing again
  with `ERR_CONNECTION_REFUSED` on what looks like a correctly-configured
  route, check this first - a stale container image is a much more likely
  culprit than a selector or timing issue.
- **Unrelated, pre-existing backend issue noticed along the way, not
  caused by or blocking this suite**: `GET
  /api/auth-service/me/preferences` returns `500` for `users-admin`
  (confirmed via direct `curl`, independent of the browser/Playwright).
  `ThemeProvider` (`src/lib/theme-context.tsx`) already fails open on this
  by design (`.catch(() => { /* deliberately silent */ })`), so it doesn't
  block login or the designer flow and isn't otherwise visible in the UI -
  left unfixed here as out of scope for a process-designer E2E suite (an
  `auth-service` bug, not a `process-designer` one).
