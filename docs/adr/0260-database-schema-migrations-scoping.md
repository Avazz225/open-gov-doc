# 0260 — Database schema migrations (scoping)

**Status:** accepted (scoping, P94-S4; no code)
**Context:** P94-S4. Schemas evolve through `Base.metadata.create_all` plus ad-hoc statements in each
service's lifespan. Is that good enough for the fleet's rolling updates and the Helm upgrade path, should
it be formalised, or should Alembic replace it?

## What exists (counted 2026-11-24)

- **Startup DDL:** 28 services run schema statements in their lifespan on every start, 153 statements
  in total:

  | Statement | Count |
  |---|---|
  | `ADD COLUMN IF NOT EXISTS` | 103 |
  | `CREATE SCHEMA IF NOT EXISTS` | 28 |
  | `CREATE INDEX` | 5 |
  | guarded backfills (`UPDATE … SET registered_at = created_at`, run only when the column was just added) | 4 |
  | `ALTER COLUMN` (`SET`/`DROP NOT NULL`, a type change) | 3 |
  | `DROP COLUMN` (`federation.installation.api_key_hash`, `ocr.ocr_config.excluded_content_types`, `workflow.federation_identity.api_key`) | 3 |
  | `DROP CONSTRAINT` | 3 |
  | `DROP INDEX` | 1 |
  | other `ALTER TABLE` | 2 |
  | `CREATE EXTENSION` | 1 |

  Heaviest are document-service (26), folder-, object-type- and workflow-service (13 each), and
  federation-hub-service (11). Some steps are data migrations in Python
  (`monitoring_service.repository.migrate_legacy_cost_keys`, seeded roles and templates).
- **Nothing records what ran.** Every start re-runs every statement; idempotency (`IF [NOT] EXISTS`,
  information-schema checks) is the only guard. No service uses Alembic.
- **Each service owns its schema** (ADR 0199, `svc_<service>` roles), so a service can alter only its
  own tables.
- **Expand/contract:** `docs/operations/rolling-updates.md` names it and states that "no table has
  needed a contract step". That is out of date: the seven drops above are contract steps. They run in
  the same release that stops using the structure, on the first start of the new version.

## Measured: a no-op migration still takes the table lock

In a scratch database: one session held an open read transaction on `t`, and a second ran
`ALTER TABLE t ADD COLUMN IF NOT EXISTS existing int` for a column that already existed.
- The no-op ALTER **waited** for the reader; it was cancelled by a 3-s `lock_timeout`.
- A plain `SELECT` from a third session, started while the ALTER waited, **queued behind it**: it took
  4 s instead of milliseconds, until the reader finished.

PostgreSQL takes `ACCESS EXCLUSIVE` before it checks `IF NOT EXISTS`. So every pod start issues up to
~25 exclusive lock requests on the service's busiest tables, and none of them changes anything after
the first start. Behind one long transaction (an export, a report, a slow request), the start stalls,
and so does every request that touches the table, on the old pods as well.

## Judged against the two upgrade paths

**Rolling updates** (compose: `scripts/rolling-update.sh`; Helm: `RollingUpdate`, HPA with several
replicas):
- **Expand steps** are compatible with old pods still running, which is good, but they pay the lock
  cost above on every pod start, not once.
- **Contract steps** run while old pods still use the structure. A dropped column breaks every old pod
  that still selects it until it is replaced. That has been harmless so far only because the dropped
  columns were already unused in the release before.
- **Several replicas starting together** run the same DDL concurrently. Each waits for the other's
  lock. A concurrent `create_all` of a new table can fail on the system catalog's unique index and
  restart the pod. This is known PostgreSQL behaviour, not reproduced here.

**Helm upgrade path:**
- No hook runs migrations; they happen when the first new pod starts. A `helm rollback` after a
  release with a contract step starts old images against a schema they don't expect.
- There is no way to run migrations once, before the rollout, with a timeout and a clear failure.

**Fleet** (ADR 0038, installations on different versions, update plans with gates):
- **The current pattern has one real strength:** an installation can skip versions. Every statement
  since the first release is still in the code and idempotent, so any old schema converges.
- **What it lacks:** a gate step can't ask "is the schema migrated?". No installation reports a schema
  version.

## Options

**A. Alembic per service:**
- **What it gives:** a version table per schema, ordered revisions with downgrades, autogenerate from
  the models, `alembic upgrade head` as a Helm pre-upgrade Job.
- **Cost:**
  - 28 migration environments.
  - A baseline revision per service that must match every existing installation's schema. They
    differ: an installation that started at an older version has the columns but no record. So the
    baseline must itself be idempotent, then `stamp`ed.
  - Autogenerate needs review on every change; downgrades are rarely trustworthy for data changes.
  - The skip-versions property survives only if no revision is ever squashed.
- **Verdict:** sound, but most of its value (autogenerate, downgrades) isn't what hurts here.

**B. Formalise the current pattern in `dms-db-base`:**
- **The shape:** each service declares an ordered list of named steps, for example
  `Step("document-0007", "ALTER TABLE … ADD COLUMN …", kind="expand")`, its seeds and backfills
  included.
- **A shared runner:**
  - takes a PostgreSQL advisory lock per schema, so concurrent replicas run it one after the other;
  - keeps a journal table `<schema>.schema_migrations` (step id, applied at, service version);
  - **skips applied steps entirely**: no statement, no lock;
  - sets `lock_timeout` with a few retries, so a start fails visibly instead of stalling the table;
  - refuses a `contract` step until the operator (or a fleet gate) confirms that no older version
    runs, for instance through a setting `DMS_SCHEMA_CONTRACT_UP_TO=<step id>`.
- **Where it runs:** the same runner works at startup and as `python -m <service>.migrate` for a Helm
  pre-upgrade Job.
- **What it keeps:** existing statements become the first steps unchanged, because they're already
  idempotent. On first contact with an existing installation the runner applies them once and records
  them. The skip-versions property stays.
- **Fleet:** `GET /healthz` or a `schema` field reports the last applied step, so a `verify` step in a
  fleet plan can check it.
- **Cost:** one runner with tests, then a mechanical conversion per service. That is the same
  AST-assisted kind of change as the bounded shutdown in P94-S3.

**C. Accept the pattern and document it:** the cheapest option. But the lock behaviour measured above
stays, as do the uncontrolled contract steps.

## Decision

**B, built later.** The lock queue measured above is a real availability risk on every rollout. The
contract steps need a gate, and a fleet needs a schema version it can verify. B delivers all three
without Alembic's baseline problem across differently aged installations, and keeps the version-skipping
the fleet relies on. Alembic stays possible later: B's journal and step ids map onto revisions if
autogenerate and downgrades are ever wanted.

**Until it is built (rules for every session):**
- A new column stays additive and nullable, or has a constant default.
- A drop, `NOT NULL` or type change is its own step in a **later** release than the one that stopped
  using the structure. It is named in the session and in `docs/operations/rolling-updates.md`.
- No unconditional `UPDATE`/backfill at startup; guard it like the `registered_at` backfills.

## Build sessions (proposed, not yet scheduled)

1. **`dms-db-base` migration runner:**
   - steps, journal, advisory lock, `lock_timeout` with retry, contract gate;
   - tests against Postgres, including two concurrent runners and a held reader lock;
   - the `migrate` entry point;
   - a Helm pre-upgrade Job template behind a flag.
2. **Conversion:** document-, folder-, object-type-, workflow- and federation-hub-service first (they
   hold 76 of the 153 statements), then the rest. The schema step goes into `GET /healthz`. The
   rolling-update doc is corrected and the contract rule written down.

## Consequences of this decision

- **No code in P94-S4.**
- **`docs/operations/rolling-updates.md`:**
  - the "no contract step so far" sentence is corrected;
  - the measured lock behaviour is noted;
  - the interim rules above are added.
- **Two build sessions are proposed for the plan.** Where they go in the order is the user's call at
  the next planning round.
