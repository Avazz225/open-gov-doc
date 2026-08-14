# config-service

**Purpose:** Configuration import/export (Concept 7.3, P12-S3): the complete
system configuration (object types including form layouts, workflows, since P14-S4 additionally
DMN 1.3 decision tables, since P14-S5 additionally business calendars, role/permission templates,
four-eyes principle settings per action type, sensor configuration, since P17-S1 additionally
Keycloak realm roles) exportable as a single JSON document and re-importable into another (or the
same, e.g. staging→production) system — versioning of the configuration schema itself, so that an
export from an older version can be imported into a newer one. **Since P17-S1** the same document
optionally carries a `manifest` (name/version/compatibility range/description/
origin/license), turning it into a named **configuration package** (14.1) — see
"Configuration Packages" below.

**Concept reference:** 7.3, 7.5, 14.1, 14.2
**Own Postgres schema:** none — pure orchestrator, every category is read/written directly at the
respective owner service (`object-type-service`, `workflow-service`,
`permission-service`, `monitoring-service`, since P17-S1 additionally `auth-service`), same
"stateless orchestrator" pattern as `webdav-connector` (P12-S1). Since **P17-S3**, nonetheless a
pure NATS **consumer** with no own stream (`ensure_stream=False`) — see "Four-Eyes Principle for
`config.import`" below.
**ADR:** [0035 — Export scope, upsert semantics, gating reuse](../adr/0035-config-service-scope-and-upsert-semantics.md),
[0040 — Delta comparison: field-level diff, no automatic cross-installation fetch](../adr/0040-config-compare-field-level-diff-no-cross-installation-fetch.md),
[0058 — Configuration packages: manifest + `realm_roles`, gateway route split](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md),
[0060 — eGov package part 2: four-eyes gaps closed](../adr/0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md)

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/config/export` | Exports a `ConfigDocument` — optionally `?categories=roles&categories=workflows` to restrict scope, otherwise all nine categories |
| `POST` | `/config/compare` | **Since P14-S1**: delta/comparison function (7.5) — body `{compare, base?, categories?, ignore_regex?}`; if `base` is missing, the service's own current live export is used as the base instance. Purely read-only/diagnostic, ungated like `GET /config/export`. `422` for an unknown category or invalid `ignore_regex` |
| `POST` | `/config/import` | Applies a `ConfigDocument` (upsert per category) — requires an `X-DMS-Principal` header with `admin.object_config` permission, otherwise `403`; an unknown `schema_version` with no migration path → `422`. **Since P17-S1 NO LONGER a public gateway route** (see "Gateway Route Split" below). **Since P17-S3** optionally gated via the four-eyes principle (`config.import`, see below) — response `ImportActionResult` (`status: "applied"\|"pending_approval"`, `result`, `approval_request_id`) instead of the previous flat `ImportResult` |
| `POST` | `/config/fleet-import` | **Since P17-S1** (previously the same route as `/config/import`): identical application logic, but exclusively for `fleet-management-service` — requires `Authorization: Bearer <DMS_FLEET_AGENT_API_KEY>` (3a/P13-S2, [ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md)), no RBAC branch. Remains the public gateway route. **Deliberately still ungated** (see below) |
| `GET` | `/healthz` | Health check (ungated) |

## Four-Eyes Principle for `config.import` (4.3/14.2, since P17-S3)

`POST /config/import` queries `ApprovalClient.requires_approval("config.import")`
at `permission-service` before applying (identical client pattern to `document-service`'s
force-unlock gate) — by default (`requires_approval=false`) behavior is unchanged: immediate
application, `status="applied"`. If the approval requirement is enabled (e.g. via the eGov
package, see `packages/egov/`), an `ApprovalRequest` is created instead (`{document, categories}`
as `payload`) and `status="pending_approval"` is returned — `result` remains `null` until a second
person confirms via `POST /approval-requests/{id}/approve`. The actual application then runs
asynchronously via a new, pure NATS consumer (`consumer.py`, `durable="config-service"`,
subscribed to `permission.approval.approved` on the `permission` stream owned by
`permission-service`, `ensure_stream=False`), which for `action_type="config.import"` invokes the
same `_apply_config_document()` application logic as the immediate path. `POST /config/fleet-import`
deliberately remains ungated — the automated fleet-agent provisioning path has no
human in the loop who could meaningfully confirm a later pending approval request
([ADR 0037](../adr/0037-fleet-management-service-agent-key-and-gateway-public-routes.md)).

Since `config-service` otherwise has no own event bus connection, the consumer requires
`DMS_NATS_URL` (`infra/docker-compose.yml`: `nats://nats:4222`) — without this variable
`BaseServiceSettings` falls back to `nats://localhost:4222`, which is unreachable in the container
(a bug found by P17-S3 itself, see [ADR 0060](../adr/0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md)).

## The nine categories

| Category | Owner service | Natural key (upsert) | Special notes |
|---|---|---|---|
| `object_types` | object-type-service | `name` | Layouts only if `is_custom=true` (2.2b) — computed smart-layout defaults are never exported |
| `workflows` | workflow-service | — (always a new version) | Only the most recent version per process definition family; the target `workflow-service` automatically re-versions on import (ADR 0027) |
| `dmn_definitions` | workflow-service | — (always a new version) | **Since P14-S4**: DMN 1.3 decision tables (7.1) — same pattern as `workflows`, only the most recent version per family, target re-versions automatically on import. Deliberately applied **before** `workflows` on import (`imports.py`), since a `businessRuleTask`'s `camunda:decisionRef` can only be resolved if the referenced DMN family already exists on the target `workflow-service` |
| `business_calendars` | workflow-service | `name` | **Since P14-S5**: business calendars for SLA deadline calculation (7.1, [ADR 0042](../adr/0042-business-calendar-script-engine-injection.md)) — unlike `workflows`/`dmn_definitions`, an ordinary upsert like `roles` (NO versioning pattern, a calendar is maintained continuously rather than versioned) |
| `roles` | permission-service | `name` | Role templates (`Role`), **not** resource-bound `role_assignment` rows |
| `approval_config` | permission-service | `action_type` | Four-eyes principle configuration (4.3) — target endpoint is already an upsert |
| `sensor_config` | monitoring-service | — (singleton + overrides) | Global default + sensor overrides (10.1, P11-S1) |
| `federation_config` | workflow-service | — (singleton) | Version compatibility range for federated workflows (7.4, P13-S3) — a `PUT` there immediately triggers a re-registration with the Federation Hub, see `docs/services/workflow-service.md` "Federation" |
| `realm_roles` | auth-service | Name (plain `list[str]`, not `list[dict]`) | **Since P17-S1** (14.1): Keycloak realm roles (e.g. `dms-poststelle`, 2.5) — unlike `roles` above (permission-service's DB-based `Role`s), a completely separate system. Application is idempotent via `create_realm_role(..., skip_exists=True)`, identical primitive to `bootstrap._ensure_dms_admin_role` |

Deliberately **not** included: "UI customizations" (branding/theming) and AD group mapping
rules — neither exists anywhere in the code (see ADR 0035), so they were not invented as empty,
fictitious categories.

## Upsert Semantics

Import matches object types, roles, and (since P14-S5) business calendars by `name` (unique
within their respective DB schema) — an already-existing name is updated rather than duplicated.
`approval_config`/`sensor_config` use their owner services' already-existing upsert endpoints
directly. Workflows/DMN definitions always count as `created` (every import creates a new
version, see above) — business calendars do NOT, they instead follow the same
pattern as roles (`created` for a new name, `updated` for an existing one). Each entry is
processed individually within a `try`/`except` — a faulty entry (e.g. an attribute that
violates a constraint on the target system) ends up in `CategoryResult.errors` without
aborting the entire import.

## Configuration Packages (14.1, since P17-S1)

A configuration package is technically still exactly a `ConfigDocument` — merely extended with an
optional `manifest` field:

```json
{
  "schema_version": "1.0",
  "exported_at": "2026-08-11T00:00:00Z",
  "manifest": {
    "name": "eGov-Konfigurationspaket",
    "version": "1.0.0",
    "compatibility_range": ">=1.0,<2.0",
    "description": "Standardkonfiguration für die deutsche öffentliche Verwaltung",
    "origin": "dms-project",
    "license": "MIT"
  },
  "object_types": [...],
  "realm_roles": ["dms-poststelle"]
}
```

`manifest` is purely descriptive — `compatibility_range` is **not** automatically checked against
the running system version (analogous to `federation_config`'s compatibility range, which is
likewise purely informational). Application runs entirely through the already-existing
`POST /config/import` (additive/upsert, repeatably applicable — 14.1 verbatim: "also applicable to
an already running, partially differently configured installation"). A `ConfigDocument` without
a `manifest` remains an ordinary 7.3 export/import as before P17-S1. Preview before application
uses the already-existing `POST /config/compare` (7.5, P14-S1) — no new endpoint, omitting `base`
automatically pulls in the service's own live export ("what would change if I import this
package"). First concrete user interface: the new admin UI page `/config-packages/`
(see `docs/services/admin-ui.md`) — before this, `config-service` had no frontend connection at
all. See [ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md) for details/reasoning.

## Gateway Route Split: `config/import` vs. `config/fleet-import` (since P17-S1)

Until P17-S1, RBAC callers (real, logged-in `config-admin` users) and the fleet agent key
(3a/P13-S2) shared the same gateway route `config-service:config/import`, marked since
ADR 0037 as public (no Keycloak token required). **Real bug found during the first
admin UI integration**: for public routes, the gateway fundamentally validates
no bearer token and never sets `X-DMS-Principal` (`gateway_service.main.proxy`) — the
RBAC branch of `_require_import_permission` was thus effectively unreachable for ANY call via
the gateway, even for real admins. Since P17-S1: `POST /config/import` is a
regular, Keycloak-token-required route (pure RBAC, no more fleet bypass); the fleet agent
instead calls the new, still-public `POST /config/fleet-import`. See
[ADR 0058](../adr/0058-konfigurationspakete-manifest-realm-roles-and-gateway-import-route-split.md) for details.

## Gating (7.3 Import)

`POST /config/import` requires the same `admin.object_config` capability as `workflow-service`'s
process definition upload (no new, own domain-admin role) — a full configuration import is an
extension of the same responsibility. Since sensor configuration write access additionally requires
`admin.monitoring` (P11-S1) and the `realm_roles` category additionally requires `admin.user_management`
(P17-S1, `auth-service`'s new `POST /realm-roles`), `config-service` bootstraps itself
**three** roles on startup (`domain-admin-config`, `domain-admin-monitoring`,
`domain-admin-users`) — idempotent self-assignment, identical pattern to `migration-service`'s
`_ensure_config_admin_permission()` (P12-S2). Since Post-Roadmap Phase 19 Session 6, `clients.py`'s
`PermissionServiceClient` additionally sends an `X-DMS-Principal: config-service` header (the
client already held `admin.user_management` via `domain-admin-users`, but sent no header) —
needed because `permission-service`'s own `POST`/`PUT /roles` has since required the same capability
([ADR 0071](../adr/0071-permission-service-self-gating.md)).

## Schema Versioning

`SCHEMA_VERSION = "1.0"` (so far the only version). `migrations.py` already defines the
extension point: a `MIGRATIONS: dict[str, Callable[[dict], dict]]` registry plus
`upgrade_to_current()`, which operates on the **raw dict** before Pydantic validates against the
current version (an older export version with fields renamed in the meantime would otherwise
already fail validation). An unreachable `schema_version` value (no migration path,
or a cycle) is rejected with `422` instead of being silently misinterpreted. Deliberately
**no** invented migration for a version that does not yet exist.

## Integration of the P11-S0 Finding (Sensor Configuration)

`monitoring-service`'s `SensorConfigEntry` was deliberately persisted **independently** in P11-S1
("Deliberately no integration with 7.3 — configuration export does not exist until P12-S3"). This
session closes that gap: `sensor_config` is now a regular export/import category like any
other.

## Integration of the P13-S3 Finding (Version Compatibility)

Concept 7.4 explicitly requires that the version compatibility range of federated installations be
"part of the already-versioned configuration schema (7.3)" - before P13-S3 it lived
exclusively in `workflow-service`'s `Settings`, changeable only via a container restart. The new
category `federation_config` closes this gap following the same pattern as `sensor_config`
(P11-S0 finding) - `WorkflowServiceClient.get_federation_config()`/`put_federation_config()`.

## Delta/Comparison Function (7.5, since P14-S1)

`POST /config/compare` compares two `ConfigDocument` exports against each other — base instance
(reference) against comparison instance, result is a directed delta report per category
(`CategoryDelta`: `only_in_base`, `only_in_compare`, `differing`, `identical`). Purely
read-only/diagnostic, changes nothing on either side (7.5). See
[ADR 0040](../adr/0040-config-compare-field-level-diff-no-cross-installation-fetch.md) for details/reasoning.

- **Matching per category**: list categories (`object_types`/`workflows`/`roles` by `name`,
  `approval_config` by `action_type`) are matched to each other via their name field;
  `sensor_config`/`federation_config` are singletons, no name matching needed.
- **Field-level comparison**, not a recursive deep diff — `differing` lists, for each matched
  entry, exactly the top-level fields that differ (`{"base": ..., "compare": ...}`),
  not a line-precise breakdown of nested structures such as object-type layouts.
- **Ignore regex** (`ignore_regex: {"<category>": "<pattern>"}`, `"*"` as global default) is
  applied to both names before matching (`re.sub`, substring removal — 7.5's example:
  numeric prefixes like `100_`/`101__` are removed, so `100_testobjekt_typ_alpha` and
  `101__testobjekt_typ_alpha` are treated as the same object). Affects **only** the
  matching — the attribute comparison of the remaining fields continues independently in full,
  the display name in the report always remains the raw base-instance value.
- **`base` optional**: if missing, `config-service` builds its own current live export as the
  base instance (`export.build_export`, the same function as `GET /config/export`) —
  use case "what would change if I import this document" (7.5).
- **Installation-specific data is structurally excluded**: license status/
  registry reachability are not part of `ConfigDocument` at all, so 7.5's exclusion rule is
  automatically satisfied.
- **CLI**: `dms config export [--category X]... [--file out.json]` and
  `dms config compare <compare.json> [--base base.json] [--category X]... [--ignore-regex '{"*": "..."}']`
  — fulfilling 7.5's "via the CLI tool (6.2) in structured, script-friendly output"
  (`-o json` returns the full `CompareResult`, the default table a
  category summary with counts).

## Deliberate Limitations

- **No selection of individual entries within a category** — only coarse-grained category
  filtering. With many accumulated workflow families (e.g. from test runs), `categories=workflows`
  therefore exports all current families; a name allowlist would be an
  obvious future feature.
- **No compatibility check between source and target system** before import (unlike
  `migration-service`'s dry run, ADR 0034) — values are applied unchanged, a
  constraint-violating entry ends up as an error in `CategoryResult.errors`.
- **No automated cross-installation fetch for `POST /config/compare`** (P14-S1, see
  ADR 0040) — both exports must already be available to the calling side (each produced via
  its own, regularly authenticated `GET /config/export` access to the respective installation,
  e.g. via `dms config export`), even if both installations participate in a shared Federation
  Hub. Deliberately scoped smaller than 7.5's optionally mentioned hub automation, in order
  to neither publicly gate `GET /config/export` nor turn `config-service` into its own
  federation participant.
- **No deep diff of nested structures** — a change deep within an
  object-type layout is reported as "the entire `layouts` field differs", not line-precisely.
- **No admin UI visualization of the comparison** in this session — CLI and raw API only,
  see ADR 0040.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DMS_OBJECT_TYPE_SERVICE_BASE_URL` | `http://localhost:8007` | object-type-service |
| `DMS_WORKFLOW_SERVICE_BASE_URL` | `http://localhost:8014` | workflow-service |
| `DMS_PERMISSION_SERVICE_BASE_URL` | `http://localhost:8004` | permission-service |
| `DMS_MONITORING_SERVICE_BASE_URL` | `http://localhost:8026` | monitoring-service |
| `DMS_AUTH_SERVICE_BASE_URL` | `http://localhost:8003` | auth-service (`realm_roles` category, since P17-S1) |
| `CONFIG_SERVICE_PORT` | `8029` | Host port in the dev compose stack |

## Tests

`test_compare.py` — pure unit tests of the comparison logic (since P14-S1), without a running container:
`normalize()`/`resolve_pattern()`, `diff_list_category()`/`diff_singleton_category()` each for
only-in-base/only-in-compare/identical/differing, the ignore-regex example from 7.5 rebuilt verbatim
(numeric prefixes, content comparison remains complete regardless), plus
`compare_documents()` across all nine categories. Since **P17-S1**: `diff_string_list_category()`
(new third diff mode for the pure `list[str]` category `realm_roles`) individually as well as as part
of `compare_documents()`.

`test_api.py` — runs like `webdav-connector`/`migration-service` against the real, running
container (no in-process `TestClient`, no mocking of neighboring services) — **therefore NOT** in
`scripts/run-tests.sh`'s `CONSUMER_SERVICES` (the container must remain reachable during the test
run, see [ADR 0060](../adr/0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md)) —
`tests/conftest.py`'s `authorized_principal` fixture temporarily assigns
`domain-admin-config` to a test principal and removes the assignment afterward. Covers: export with/without
category filter, unknown category (`422`), import without/with wrong principal (`403`), unsupported
`schema_version` (`422`), role upsert (create→update by name),
four-eyes-principle config upsert, a full export→re-import round trip, plus the
`federation_config` import (actually affects the running `workflow-service` container). Since
**P14-S1**: `POST /config/compare` against itself (no differences), without `base` (pulls the
service's own live export), with real content differences, with ignore-regex matching. Since
**P17-S3**: `status="applied"` by default, the `config.import` four-eyes gate returns
`pending_approval` and imports nothing (yet) (`test_import_with_approval_required_defers_execution`).
`test_consumer.py` (new) — pure unit test of `consumer.make_handler` with a fake `apply_import`
callback instead of real DB/downstream calls: an approved `config.import` correctly
invokes the callback, foreign action types are ignored, a missing `document` in the payload is
logged instead of crashing, a failing callback does not propagate (no unacknowledged NATS message)
(same case compared with/without regex), invalid regex → `422`, unknown category → `422`.
Since **P14-S4**: `dmn_definitions` in the export's default category list,
creating `dmn_definitions` repeatedly under the same name automatically produces a new
version each time (`created: 1` on every call, same pattern as `workflows`), verified live against
the running `workflow-service` container. Since **P14-S5**: `business_calendars` in the
export's default category list, an import under a new name counts as `created`, a
repeated import with a changed `non_working_dates` under the same name counts as `updated`
(upsert semantics, unlike `dmn_definitions`), verified live against the running `workflow-service`
container. Since **P17-S1**: the fleet-agent-key bypass moved from `/config/import`
to its own tests for `/config/fleet-import` (success with correct key, `403` for wrong/
missing key), a new test explicitly confirms that a fleet agent key on
`/config/import` NO LONGER works (RBAC-only), plus a `realm_roles` import/export round trip
(verifies live against the running `auth-service` container that the realm role exists in
Keycloak).

**`tools/cli`**: `test_config_commands.py` covers `dms config export` (JSON output, file export,
`--category` query parameter) and `dms config compare` (reads file(s), sends the correct
request body including optional `base`/`ignore_regex`, table vs. JSON output) — mocked
gateway (`httpx.MockTransport`), no running stack needed.
