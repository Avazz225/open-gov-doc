# tools/cli — DMS CLI

**Responsibility:** Full command-line tool (6.2) — a client against the API gateway
(3.5), conceptually modeled on `oc` (OpenShift). Every call goes through the same
`/api/{service_type}/{path}` route with a bearer token as the web UIs (`apps/admin-ui`,
`apps/user-ui`), so the CLI respects the same permissions/protection levels (RBAC,
scope locks, four-eyes, emergency shutdown) with no bypass path of its own.

**Concept reference:** 6.2
**Not a service** — no own Postgres schema, no `main.py`/FastAPI, no entry in
`infra/docker-compose.yml` (see "Docker" below). Lives under `tools/cli/` instead of
`services/<name>/`, the same rationale as `apps/*` in `docs/service-template.md`: no
backend service, different tooling conventions.

## Scope decision (P8-S3)

Concept 6.2 lists a broad feature set (configuration import/export, query & trace
console, migration/transfer operations, object types/constraints/workflows,
registry/plugin orchestration status, license status, user/role management,
backup/restore, delta/comparison runs). At the time of this session (after phase 8), only
part of it has a real backend — the rest belongs, per `IMPLEMENTATION_PLAN.md`, to later
phases. Instead of building stubs or silently shrinking the scope, this document
honestly records what is covered and which future phase each remaining item is waiting on:

| Concept bullet | Status | CLI coverage |
|---|---|---|
| Query & trace console (6.1) | ✅ `query-service` | `dms query ...` |
| Object types/constraints (2.2) | ✅ `object-type-service` | `dms object-type ...` |
| Workflows (7.1) | ✅ `workflow-service` | `dms workflow ...` |
| User/role management (4.4/4.1/4.6) | ✅ `auth-service`/`permission-service` | `dms user ...`, `dms role ...` |
| Registry status (3.2) | ✅ `registry-service` | `dms registry status` |
| Plugin orchestration status (3.8) | ❌ does not exist (phase 10) | — |
| Migration/transfer operations (7.2) | ❌ generic 7.2 service does not exist (phase 12, P12-S2) | `dms archival ...` covers the **closest real equivalent**: `archival-service`'s transfer-to-archive transfers (5.6) — thematically related (lock→copy/package→verify→release), but not functionally the same as 7.2 |
| Configuration import/export (7.3) | ✅ `config-service` (retrofitted into the CLI since P14-S1) | `dms config export [--category]... [--file]` |
| License status (9.3) | ❌ does not exist (phase 9) | — |
| Backup/restore (10.4) | ❌ does not exist (phase 11) | — |
| Delta/comparison runs (7.5) | ✅ `config-service` (P14-S1) | `dms config compare <compare.json> [--base] [--category]... [--ignore-regex]` |

Same pattern as P7-S3's handling of the then-also-missing 7.2 template: scope
honestly limited to real endpoints, the rest named here and in `PROGRESS.md` as an open point.
**P14-S1 addendum**: `config-service` itself has already existed since P12-S3 (7.3), but was
not yet connected to the CLI until now — this gap was closed directly in the same session, since without
`dms config export` there would be no input file for the new `dms config compare` command (7.5).

## Architecture decisions

- **Python + Typer instead of a new ecosystem** — fits the existing `uv` monorepo stack.
  The root `pyproject.toml` had `tools/*` added to `[tool.uv.workspace].members` for this
  (previously only `libs/*`/`services/*`).
- **Gateway client 1:1 like `apps/*-ui/src/lib/api.ts`** — `{gateway_url}/api/{service_type}/{path}`,
  `Authorization: Bearer <token>`, no direct backend addresses (`tools/cli/src/dms_cli/client.py`).
- **Credential storage in `~/.dms/credentials.json`** (chmod 600) instead of a
  system keyring — works identically on every platform without extra dependencies.
  `dms login` (password grant via `POST /api/auth-service/login`, like the web UIs) writes
  the access/refresh token; `dms logout` deletes the file.
- **Transparent 401 refresh + retry** (`GatewayClient._refresh_once`) — the web UIs currently
  call their own `refreshToken()` function nowhere (verified: 0 call sites in
  `apps/admin-ui`/`apps/user-ui`), irrelevant so far for a short-lived browser tab. For a
  CLI, whose access token expires across many short process invocations, an automatic
  refresh is however necessary, otherwise `dms login` would need to be rerun before
  practically every other call.
- **`DMS_GATEWAY_URL`/`DMS_TOKEN` env vars override the file** — the CI/CD path: a
  pipeline injects a token obtained elsewhere, without the CLI writing anything to a
  file. See "Open Points" for why there is (still) no `client_credentials` grant.
- **Domain organization into separate `commands/<domain>.py` modules** (`query`, `object_type`,
  `user`, `role`, `registry`, `workflow`, `archival`, plus `auth`/`config`), each with its own
  Typer sub-app. Fulfills Concept 6.2's "structured modularly... organized into
  domain-specific subcommands" structurally — a literal split into separately shippable
  single binaries is a later packaging decision, not a blocker for this session.
- **Output format**: default a dependency-free table (`output.py`), global `--output json`/`-o
  json` for scripts — fulfills "scriptable output formats such as JSON" directly, without
  introducing a rendering package (`rich` or similar).
- **Four-eyes/manipulation identical to the admin UI** — `dms query manipulate dry-run`/`execute`
  + `dms query approvals list`/`approve` talk to the same `query-service`/`permission-service`
  endpoints as `QueryConsoleView`'s `ManipulationSection` (P8-S2b), including the same
  hardcoded list of the three curated action types (duplicated rather than imported
  cross-service — service isolation, see `CONTRIBUTING.md`). Concept 6.1 literally requires
  that "the same query language and the same protection levels" apply in the UI and CLI.
- **Docker without a compose entry** — its own `Dockerfile` (uv image, `ENTRYPOINT ["uv", "run",
  "dms"]`) for the CI/CD use case (`docker run dms-cli ...` without a local Python
  environment), but **no** entry in `infra/docker-compose.yml`: no health endpoint, no long-running
  "up" state, the compose restart pattern does not fit a one-shot command.

## Command overview

| Domain | Commands |
|---|---|
| Login | `dms login [--username] [--password] [--gateway-url]`, `dms logout`, `dms whoami` |
| Configuration | `dms config show`, `dms config set-gateway-url <url>` |
| Configuration import/export & comparison (7.3/7.5) | `dms config export [--category]... [--file out.json]`, `dms config compare <compare.json> [--base base.json] [--category]... [--ignore-regex '{"*": "..."}']` |
| Query & trace (6.1) | `dms query events list [--actor] [--subject] [--event-type] [--since] [--until] [--limit]`, `dms query text "<...>"`, `dms query manipulation-mode status\|activate [--minutes]\|deactivate`, `dms query manipulate dry-run --action <type> --param k=v...`, `dms query manipulate execute --dry-run-token <token>`, `dms query approvals list\|approve <id> [--approved-by]` |
| Object types (2.2) | `dms object-type list\|get <id>\|create -f <file>\|update <id> -f <file>\|delete <id>` |
| Users (4.4) | `dms user list\|create --username --email --first-name --last-name [--password]\|delete <id>` |
| Roles (4.1/4.6) | `dms role list`, `dms role assignment list [--principal] [--resource]\|create ...\|delete <id>` |
| Registry (3.2) | `dms registry status [service_type]` |
| Workflow (7.1) | `dms workflow definitions list\|get <id>`, `dms workflow instances list [--status]\|get <id>\|tasks <id>\|complete-task <instance> <task> --completed-by [-f data.json] [--signature-id]` |
| Transfer to the archive (5.6, see table above) | `dms archival transfers list [--status]\|get <id>\|retrieve <id>`, `dms archival case-transfers list\|get <id>\|package <id> --out <path>` |

Every command accepts the global `--output table\|json` (`-o`), which must be given
**before** the subcommand (`dms -o json query events list`).

## Open Points

- **No `client_credentials` grant for real service accounts** — `auth-service`'s Keycloak
  client has `serviceAccountsEnabled: False` (`bootstrap.py`). A clean service account per
  CI/CD pipeline would need its own confidential Keycloak clients (client management API) — an
  independent, multi-session feature, not an extension of this session. As a stopgap:
  the `DMS_TOKEN` env var (see above).
- The 7.2/9.3/10.4 bullets from Concept 6.2 still have no backend — see the table above, each
  with a reference to the intended phase. No CLI code exists for them. 7.3/7.5 have been
  covered since P14-S1 (`dms config export`/`dms config compare`).
- **`dms config compare` reads both exports from local files**, no automated
  cross-installation fetch — a deliberate boundary, see `docs/services/config-service.md`
  "Delta/comparison function" and [ADR 0040](../adr/0040-config-compare-field-level-diff-no-cross-installation-fetch.md).
- No reject command for four-eyes requests (`dms query approvals`) — the same deliberate
  gap as in the admin UI (P8-S2b): rejection is already possible generically via
  `permission-service`, no console-specific added value in this session.

## Tests

`tools/cli/tests/` — **77 tests** (previously 70, +7 since P14-S1: `dms config export`/
`dms config compare`, see below): `GatewayClient` (401 refresh retry, error paths), credential
storage (env override, file permissions), output formatting, plus representative
happy-path/error-path tests per domain module via `typer.testing.CliRunner` + `httpx.MockTransport` (no real
network). `uv run pytest tools/cli/tests`, `uv run ruff check tools/cli`.
