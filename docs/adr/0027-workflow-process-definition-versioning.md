# 0027 — Workflow Service: Process definition versioning via `name` as family key

**Status:** accepted
**Context:** P6-S8 (Process Designer). During the plan check-in, the user decided that real process versioning should be retrofitted already in this session, rather than remaining an open point (documented since P6-S1: "a re-upload under the same `name` is rejected (409), not created as a new version"). The new, standalone Process Designer needs a sensible "save" semantics for already-imported/edited process definitions — without versioning, every re-save would either have to fail (409) or force an artificially different name.

## Decision

**`name` changes from a globally unique identifier to a process-family key.** New column `process_definition.version` (integer, default 1). Uniqueness now applies to `(name, version)` instead of `name` alone (`UniqueConstraint`/unique index `ux_process_definition_name_version`). `POST /process-definitions` under an already-existing name automatically computes `max(version per name) + 1` server-side and creates a new row — the previous `409` rejection path (`DuplicateNameError`) is removed without replacement; two definitions with the same name are now the normal case, no longer an error condition.

**`GET /process-definitions` returns by default only the latest version per name** (`SELECT DISTINCT ON (name) ... ORDER BY name, version DESC`, Postgres-specific). A new optional `?name=` filter instead returns the full version history of a single family, newest first.

**Process instances remain bound, unchanged, to a specific version**: `process_instance.process_definition_id` still references a specific `id`, not a family — a new version never retroactively affects already-running or already-completed instances. `POST .../{id}/instances` and `DELETE /process-definitions/{id}` therefore **did not need to change**.

**Ad-hoc migration for existing databases** (no Alembic at this early stage, see `CONTRIBUTING.md`): `ALTER TABLE ... ADD COLUMN IF NOT EXISTS version INTEGER NOT NULL DEFAULT 1`, `ALTER TABLE ... DROP CONSTRAINT IF EXISTS process_definition_name_key` (Postgres' own default name for the previous inline `UNIQUE` column constraint, empirically confirmed on the running dev stack via `\d workflow.process_definition`), `CREATE UNIQUE INDEX IF NOT EXISTS ux_process_definition_name_version ON (name, version)` — a unique index instead of a unique constraint, since Postgres has no `ADD CONSTRAINT IF NOT EXISTS` (unlike `CREATE INDEX IF NOT EXISTS`), but both mechanisms provide the same uniqueness guarantee.

## Rationale

- **Name as family key instead of a separate `process_key` field**: avoids an additional identification field that the user would have to maintain separately — the already-existing, user-assigned display name takes on this role directly. This lets the Process Designer offer "save" without special-case logic: leave name unchanged → new version, change name → new family.
- **No change needed to `process_instance`/instance start**: instances already point to a specific `id`, not a name — versioning is therefore a self-contained extension of `process_definition` alone, with no retroactive effect on the already-production execution path (ADR 0019).
- **`DISTINCT ON` instead of a subquery/window function for "latest version per name"**: a Postgres-native pattern already used elsewhere in this project (`INSERT ... ON CONFLICT DO NOTHING` in the reference-number generator is a similar example of "deliberately Postgres-specific rather than DB-agnostic," since this project consistently develops against Postgres).
- **Unique index instead of unique constraint for the idempotent migration**: Postgres does not support `ADD CONSTRAINT IF NOT EXISTS` (unlike MySQL/the older behavior of some other databases), but does support `CREATE UNIQUE INDEX IF NOT EXISTS` — functionally equivalent for enforcing uniqueness, without needing a `DO $$ ... EXCEPTION ...` PL/pgSQL workaround.

## Consequences

- **No race-condition lock on version assignment** (`SELECT max(version)` followed by `INSERT`, no `SELECT ... FOR UPDATE`) — deliberately the same, already-established simplification as `document-service`'s version-number assignment (`checkin_version`): a rare, genuine concurrency conflict between two parallel save attempts under the same name would fail at the `(name, version)` unique constraint (not caught, results in a `500`) rather than being automatically resolved — accepted for a basic framework without high-frequency parallel process-definition saves.
- **No UI/API to explicitly "roll back" to an older version as the new latest version** — an older version can still be opened/started, but "save this old version again as the latest" would require a manual re-upload of its `bpmn_xml`, not a dedicated "rollback" endpoint.
- **No deletion of whole families** — `DELETE /process-definitions/{id}` remains per version; a family with multiple versions must be deleted one at a time (each still blocked as long as it has its own instances).
