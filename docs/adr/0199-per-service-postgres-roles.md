# 0199 — Per-service Postgres roles, GRANT restricted to own schema

**Status:** accepted
**Context:** P68-S1 (Phase 68, "Infrastructure Hardening" — first session of the eighth gap-analysis
round's plan). Konzept 3.1's explicit requirement — "technical enforcement: a DB user per service,
`GRANT` restricted exclusively to its own schema" — was never built. Schema separation itself was already
real (`infra/postgres-init/001-schemas.sql`'s convention, one schema per service), but every one of ~28
services connected with the identical shared `dms` superuser credential, so the separation was
convention-only, not technically enforced (the same risk Konzept 3.1 names verbatim: "prevents accidental
coupling even in the presence of bugs"). Flagged in the plan itself as the largest-blast-radius session of
the whole round, with the rollout shape (big-bang vs. phased) deliberately left to be decided at session
start — the user was asked directly and chose big-bang: all services in one session.

## Decision

**One dedicated Postgres role per service, each owning exactly its own schema**, connecting with that
role instead of the shared `dms` superuser:

- 28 services actually use Postgres (confirmed by grepping every `services/*/src/*/models.py` for
  `make_declarative_base("<schema>")`); 4 (`cmis-connector`, `config-service`, `gateway-service`,
  `webdav-connector`) never open a DB connection at all and are unaffected.
- Role naming: `svc_<schema>` (e.g. `svc_document`, `svc_case` — `case` being a reserved word, quoted as
  `"case"` in the schema DDL, unquoted as an identifier in the role name).
- `infra/postgres-init/002-service-roles.sh` (new, alongside the existing `001-schemas.sql`) creates all
  28 roles and pre-creates all 28 schemas with `CREATE SCHEMA IF NOT EXISTS <schema> AUTHORIZATION
  svc_<schema>` — schema ownership is what keeps `Base.metadata.create_all` and the ad-hoc `ALTER TABLE
  ... ADD COLUMN IF NOT EXISTS ...` migrations (no Alembic at this project stage, see `CONTRIBUTING.md`)
  working unchanged for tables the role itself creates.
- A `.sh` script rather than `.sql`, so each role's password comes from an env var
  (`POSTGRES_PASSWORD_<SCHEMA>`, same `${VAR:-dev-default}` convention `docker-compose.yml` already uses
  for `POSTGRES_PASSWORD` itself) instead of one value hardcoded and shared across every environment.
- `infra/docker-compose.yml`: all 28 services' `DMS_POSTGRES_DSN` now use their own role/password instead
  of the shared `${POSTGRES_USER:-dms}:${POSTGRES_PASSWORD:-dms_dev_only}` string; the `postgres` service
  itself gained the 28 `POSTGRES_PASSWORD_<SCHEMA>` env vars for the init script to read.
- `infra/k8s/dms/`: mirrored — new `templates/postgresql.yaml` env vars sourced from 28 new per-service
  Secrets (`templates/secrets.yaml`, same `existingSecret`-if-set-else-generate convention as the existing
  Postgres/Keycloak/MinIO secrets, ADR 0100), new `values.yaml` `postgresql.servicePasswords`/
  `serviceExistingSecrets` maps, `templates/_helpers.tpl` gained a service-name→schema-name lookup and a
  per-service-aware `dms.postgresDsn`. **Bundled Postgres only** — external Postgres (`postgresql.enabled:
  false`) keeps the single global credential unchanged, since an external DBA would need to have
  provisioned exactly these 28 identically-named roles themselves, an assumption this chart doesn't make.
- `scripts/run-tests.sh`: test runs now exercise the SAME narrowed per-service role production uses (a
  new `SERVICE_PG_ROLE` lookup table sets `TEST_POSTGRES_DSN` per service before that service's `pytest`
  call) rather than the superuser — proving the narrowing doesn't break anything is the whole point of
  this session; testing against the superuser would prove nothing. The script also grants the same
  schema-ownership/`CREATE ON DATABASE` privileges within `dms_test` (which doesn't exist at Postgres's
  first boot, so the init script's own grants on `dms` don't reach it).

**A genuine, discovered-live trade-off: every role also needs `GRANT CREATE ON DATABASE`,** not just
ownership of its own schema. The original design assumed `CREATE SCHEMA IF NOT EXISTS` would short-circuit
on an already-existing schema before any permission check — confirmed wrong via a real, live failure
during this session (every service failed to start with `InsufficientPrivilegeError: permission denied
for database dms` on its own, already-existing schema). Postgres checks `CREATE`-on-database privilege
**before** checking whether the schema exists; `IF NOT EXISTS` only suppresses the "already exists" error
afterward. Since every service unconditionally runs `CREATE SCHEMA IF NOT EXISTS <own>` at every startup
(no Alembic), each role needs this grant. This is a deliberate, documented trade-off, not a regression
from the intended design: a compromised or buggy service could create an **additional, empty** schema of
its own choosing, but still cannot read or write into any **other** service's existing schema — the
actual data-isolation guarantee Konzept 3.1 asks for remains fully intact.

## Rationale

- **Why `GRANT CREATE ON DATABASE` instead of removing the `CREATE SCHEMA IF NOT EXISTS` call from every
  service**: that call lives in 28 services' own `main.py`/`conftest.py` (and, per `CONTRIBUTING.md`, is
  the established, load-bearing "no Alembic at this project stage" migration mechanism) — removing or
  conditionalizing it in 28 places would be a materially larger, riskier change than accepting the
  documented trade-off above, for a residual risk (an extra empty schema) that doesn't touch the actual
  isolation guarantee.
- **Why schema ownership (not just `SELECT/INSERT/UPDATE/DELETE` grants) for the connecting role**:
  `ALTER TABLE ... ADD COLUMN` (the project's actual migration mechanism, used in production `main.py` by
  20 of the 28 services already) requires table ownership in Postgres — there is no standalone `ALTER`
  privilege to grant. Making the role the schema's owner means every table it creates via `create_all` is
  automatically owned by it too, so `ALTER TABLE` keeps working with zero code changes anywhere.
- **Why no cross-schema blocker was found**: confirmed by grepping every `ForeignKey("<schema>.<table>"...)`
  reference across all 28 services' `models.py` — every one references only its own containing service's
  schema. No service's raw SQL queries another service's schema either (only `information_schema.*`
  catalog views, which need no special grant). Matches Konzept 3.1's own stated architecture ("other
  services query data exclusively via the API of the respective owner service") — this session makes that
  already-true behavior technically enforced, it doesn't change any actual query pattern.
- **Why per-service test credentials in `scripts/run-tests.sh` instead of leaving tests on the
  superuser**: a narrowed-permission rollout that only tests under a superuser proves nothing about
  whether the narrowing actually works — the whole point of this session's own test-suite verification
  step is to exercise the real, narrowed identity each service will actually run as in production.
- **Why external Postgres keeps the single shared credential, not per-service roles too**: this chart has
  no way to know or provision a real external DBA's role-naming scheme, and requiring an operator's
  existing Postgres instance to have 28 exactly-named roles pre-created before this chart would even start
  is an unreasonable, undocumented expectation for a "bring your own Postgres" deployment. A future
  session could add a documented manual-provisioning guide for that path if a real operator need arises;
  not attempted here.
- **Why no cross-database (not just cross-schema) isolation was pursued (e.g. a Postgres database per
  service instead of a schema)**: out of scope — Konzept 3.1 itself specifies schema-level isolation
  within one shared database, matching the architecture every service's `models.py` already assumes
  (`MetaData(schema=...)`, not a per-service connection string to a different database name). Not
  reconsidered here.

## Consequences

- `infra/postgres-init/002-service-roles.sh` (new), `infra/docker-compose.yml` (postgres service +28 env
  vars, all 28 DB-using services' `DMS_POSTGRES_DSN` changed).
- `infra/k8s/dms/files/postgres-init/002-service-roles.sh` (new, manually-synced copy, same convention as
  `001-schemas.sql`'s existing copy), `infra/k8s/dms/templates/postgresql.yaml` (ConfigMap glob extended
  to `*.sh`, +28 env vars), `infra/k8s/dms/templates/secrets.yaml` (+28 generated secrets, looped),
  `infra/k8s/dms/templates/_helpers.tpl` (`dms.postgresServiceSchema`, `dms.postgresServiceRole`,
  `dms.postgresServiceSecretName`/`Key`, `dms.postgresDsn`/`dms.baseEnv` made per-service-aware),
  `infra/k8s/dms/values.yaml` (+`postgresql.servicePasswords`/`serviceExistingSecrets`, 28 entries each).
  **Validated via `helm lint` (clean) and `helm template` (rendered and inspected: `document-service`
  resolves to `svc_document`/its own secret, `case-service` to `svc_case`, `gateway-service` correctly
  falls back to the shared credential since it has no schema, the postgresql Deployment carries all 28
  `POSTGRES_PASSWORD_<SCHEMA>` vars, the init ConfigMap carries both init files)** — no live k8s cluster
  was available in this environment to deploy the chart for a true end-to-end k8s verification; this is an
  accepted, documented gap for a future session with real cluster access.
- `scripts/run-tests.sh`: new `SERVICE_PG_ROLE` lookup table, per-service `TEST_POSTGRES_DSN` override
  inside the test loop, plus schema-ownership and `GRANT CREATE ON DATABASE` steps for `dms_test`.
- **Existing, already-running deployments (including this project's own long-running dev stack) do NOT
  get this automatically** — `docker-entrypoint-initdb.d` scripts (both `.sql` and `.sh`) only run once,
  on a completely fresh data volume. An existing deployment upgrading to this needs the equivalent
  statements applied manually: `CREATE ROLE` + `GRANT CONNECT, CREATE ON DATABASE` + (since
  `CREATE SCHEMA IF NOT EXISTS AUTHORIZATION` is a no-op for an already-existing schema) `ALTER SCHEMA
  <schema> OWNER TO <role>` plus `ALTER TABLE`/`ALTER SEQUENCE ... OWNER TO <role>` for every already-
  existing object in that schema. Applied exactly this way, live, to this project's own dev stack during
  this session (see Consequences' live-verification paragraph below) — the same statements an operator
  upgrading a real installation would need to run once.
- **Live-verified against the real running stack — the actual dev Postgres data volume, not a fresh
  one**: roles created, ownership of all 28 schemas and every existing table/sequence within them
  transferred from the previous superuser to the corresponding new role (confirmed via `pg_namespace`/
  `pg_tables` ownership queries before/after), the full stack (`docker compose up -d`, all containers)
  restarted cleanly with the new per-service `DMS_POSTGRES_DSN` values, and real API calls against
  `document-service`/`auth-service`/`workflow-service`/`registry-service` confirmed real DB reads/writes
  still work end to end under the narrowed credentials.
- Full backend regression (`scripts/run-tests.sh`, all services): initially surfaced the missing `GRANT
  CREATE ON DATABASE` on `dms_test` too (same root cause as the production-side discovery above, just a
  second instance of the same missed grant) — fixed in the same session, full suite re-run to confirm
  green afterward.
- `docs/services/*.md`, `CONTRIBUTING.md`: not modified in this session — the actual mechanism (`CREATE
  SCHEMA IF NOT EXISTS`/`create_all`/`ALTER TABLE ADD COLUMN IF NOT EXISTS`, "no Alembic") is unchanged
  from each individual service's own code perspective; only the *credential* each service connects with
  changed, which is purely an `infra/`-level concern already covered by this ADR and the updated
  `infra/postgres-init/README`-equivalent comments in the files themselves.
