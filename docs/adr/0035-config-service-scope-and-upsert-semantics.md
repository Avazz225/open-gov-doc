# 0035 — Config Service: export scope, upsert semantics, gating reuse

**Status:** accepted
**Context:** P12-S3 (Concept 7.3, "Configuration Import/Export"). 7.3 requires: "Complete
system configuration (object types, constraints, workflows, role/permission templates, four-eyes
settings per action type, UI customizations) exportable as a JSON document ... re-import into
another (or the same, e.g. staging→production) system possible ... versioning of the
configuration schema itself, so that an export from an older version can be imported into a
newer one." `config-service` itself has **no Postgres schema of its own** — every category is
read/written directly at the respective owner service (domain-owner principle), the same basic
pattern as `webdav-connector` (P12-S1, "stateless orchestrator").

## Decision

**Five real categories instead of the literal 7.3 list**: `object_types`, `workflows`, `roles`,
`approval_config`, `sensor_config`. "UI customizations" (branding/theming) and an AD group
mapping rule exist nowhere in the code — after an exhaustive grep across the entire tree, both
were deliberately **not** invented as fictional, empty categories, but honestly left out (the
same discipline already applied at P12-S2's dry-run limits).

**`is_custom`-filtered layout export**: `object-type-service`'s `GET /object-types/{id}/
layouts/{purpose}` returns either a real stored override (`is_custom=true`) or a purely computed
"smart layout" (`is_custom=false`) if none was ever explicitly saved (Concept 2.2b). Only
`is_custom=true` layouts go into the export — otherwise a re-import would incorrectly freeze an
automatically derived default as a permanent override on the target system, even though the
source system itself never set one.

**Workflows: only the latest version per family** (`workflow-service`'s `GET
/process-definitions` already returns exactly that) — not the full version history. A re-import
automatically creates a new version under the same name at the target `workflow-service` anyway
(existing versioning behavior, ADR 0027), so upsert semantics do not apply here for "created" vs.
"updated" — every workflow import deliberately counts as `created`.

**Upsert-by-name for object types and roles**: both names are unique within their respective DB
schema (existing constraints, not newly introduced). Import matches by `name` against the
target installation — if the name already exists, it is updated rather than duplicated (a core
requirement for the staging→production use case, where a repeated import must not accumulate
duplicates). `approval_config` and `sensor_config` are already real upsert endpoints on the owner
service side (`PUT /approval-config/{action_type}`, `PUT /sensor-config/global`), so no
additional pre-check is needed.

**New `PUT /roles/{role_id}` endpoint in `permission-service`**: previously did not exist (only
`POST`/`GET`) — a real, previously unknown gap, uncovered while building the upsert logic for
`roles`. `name` remains immutable on update (a natural key for configuration reconciliation).

**Gating via the existing `admin.object_config` capability instead of a new domain-admin
role**: `POST /config/import` requires the same permission as `workflow-service`'s
process-definition upload — a full configuration import is an extension of the same "object-
type/workflow configuration" responsibility, not a separate new domain. `config-service` bootstraps
itself at startup with **both** roles needed for this (`domain-admin-config` AND
`domain-admin-monitoring`, since sensor-configuration write access additionally requires
`admin.monitoring`, P11-S1) — the same idempotent self-assignment pattern as `migration-service`'s
`_ensure_config_admin_permission()` (P12-S2).

**Empty `MIGRATIONS` registry as an extension point instead of fictional migrations**: there was
previously only `SCHEMA_VERSION = "1.0"`. `migrations.py` already defines `upgrade_to_current()`
(a loop with cycle detection, `422` for an unknown/unreachable version) and the registry dict,
but **no** invented migration logic for a version that does not yet exist — matches the
project-wide discipline of not building for hypothetical future requirements.

**`payload: dict` instead of direct `ConfigDocument` validation at the import endpoint**: the
schema migration must be able to operate on the raw dict *before* Pydantic validates against the
current version — an older export version with fields since removed/renamed would otherwise
already fail at validation, before `upgrade_to_current()` even gets a chance to run.

## Rationale

- **No Postgres schema of its own**: every category already has an owner service with a
  complete CRUD API — a duplicated `config` schema would be a pure copy with no added value and
  a second source of truth that could drift out of sync.
- **Honest omission instead of invention**: introducing "UI customizations"/AD group mapping as
  empty, non-functional placeholder categories would have created the impression of an already
  existing capability that does not exist.
- **`is_custom` filtering**: an export/import cycle must faithfully reflect the observable state
  of the source system — a smart-layout default is not a configuration decision made by an
  admin and therefore does not belong in a "configuration."

## Consequences

- **Deliberate limit: no selection of individual workflow/object-type entries within a
  category** — only coarse-grained category filtering (`?categories=roles&...`). For a
  development database with hundreds of workflow families accumulated across many test runs
  (observed for real: 317 in this session's verification export), `categories=workflows`
  therefore exports **all** current families — for a production staging→production migration, a
  name allowlist would be an obvious future feature, deliberately not built here (no need per the
  7.3 text, which only requires "complete system configuration").
- **Deliberate limit: no conflict detection for contradictory `allowed_parent_types`/
  constraints between source and target system** — an import applies values unchanged; a full
  compatibility check like `migration-service`'s dry run (ADR 0034) would be a standalone
  feature.
- **Precedent for future categories**: any new exportable configuration type (e.g. a future
  branding feature) follows the same pattern — owner-service client, `is_custom`-style filtering
  where applicable, upsert-by-natural-key, its own entry in `CATEGORIES`.
