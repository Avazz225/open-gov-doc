# query-service

**Responsibility:** Central query & trace console (6.1) — a federated, RBAC-filtered read-access layer over other services' read models (P8-S1), since P8-S2 additionally the manipulation side: protection switch, dry run, optional/mandatory four-eyes principle for a curated catalog of structural manipulation actions, since **P8-S2b** fully wired into the Admin UI, since **P8-S3** also usable via the CLI tool (6.2, `dms query ...`) — see [`docs/tools/cli.md`](../tools/cli.md).

**Concept Reference:** 6.1
**Own Postgres schema:** `query` (since P8-S2, table `manipulation_mode_status` — protection switch state, genuinely own state, no duplication of foreign read models). The read side (P8-S1) remains stateless.

## Architecture Decisions

- **`pglast` (GPL-3.0-or-later) is not shipped** — clarified in P8-S0 ([ADR 0031](../adr/0031-query-konsole-pglast-plugin.md)). `query_service/parser.py` only defines the interface (`ParserPlugin.parse(query_text) -> ParsedQuery`, `load_parser_plugin(module_path)`); a real implementation (e.g. based on `pglast`) lives entirely outside this repo, exactly like the KDBX plugin decision in ADR 0029. The load/execution mechanics are tested via a test-only fake plugin (`tests/fixtures/fake_parser_plugin.py`), which explicitly is **not** real SQL grammar.
- **Two-track query language instead of all-or-nothing**: `GET /query/events` (structured filter parameters, no parser needed) delivers the console's full core value immediately, even without an installed plugin. `POST /query` (free-form, psql-like text) returns `501` as long as `DMS_QUERY_PARSER_PLUGIN_MODULE` is not set. Unlike KDBX (a niche export format), a completely non-functional console until plugin installation would be disproportionate here.
- **Only `audit-service` as a data source in this session** — "table" `events`. Concept 6.1 also names reporting/monitoring read models as possible sources; these are structurally additive to retrofit (a new "table" in the same dispatcher), but not part of P8-S1, since only audit-service already has a generic filter API.
- **RBAC/scope-lock filtering of result rows** (concept 6.1, literally: "a query can never see... more than the executing person would be allowed to anyway") — a gap that neither `audit-service`'s raw `GET /events` nor `reporting-service`'s forensic trace endpoint close (`reporting-service` has checked its own, narrower `reporting.forensic_trace` capability since Post-Roadmap Phase 19 Session 7, [ADR 0072](../adr/0072-archival-reporting-rbac.md); `audit-service`'s `GET /events` only gained its own basic `audit.read` check much later, Post-Roadmap Phase 38 Session 2, see `docs/services/audit-service.md` — before that it had none at all. Either way, neither filters any individual result row by the calling principal's permissions — exactly this row filtering is query-service's own contribution). `filtering.py` resolves a `resource_id` per event: since **Post-Roadmap Phase 39 Session 4** ([ADR 0154](../adr/0154-document-per-document-resource-case-list-filtering-org-hierarchy-case-scope.md)), both `document-service` AND `folder-service` events carry their own real `resource_id` directly as `subject` — a `document-service` event previously needed an extra `GET /documents/{id}` round trip to resolve its `folder_id` as a proxy resource, since only folders were real `permission-service` `ResourceNode`s; documents now are too, so the resolution is a direct pass-through for both, and `DocumentClient` is no longer a dependency of this filtering path at all. All other categories (workflow/case/auth/signature/notification/registry/permission-on-non-folder/...) are **not resolvable** and are hidden fail-closed — except for the activated superuser (4.6, the only exception provided for in the concept). A deliberate scope boundary, not a silent gap: a generic object permission for every conceivable domain does not exist.
- **`permission-service`'s `POST /check/batch` already combines RBAC and scope locks (4.7) in a single response** — a 1:1 reused pattern from `search-service`. Resolved `resource_id`s are deduplicated and queried in parallel (`asyncio.gather`).
- **The `admin.query_console` role already existed but was unused** — `permission-service`'s `DOMAIN_ADMIN_ROLES` catalog has contained the entry `domain-admin-query-console` since the original role seeding. `query-service` is the first consumer to actually check this permission (`_require_query_console`, identical gate pattern to `workflow-service._require_object_config`).
- **Superuser exception without a header shortcut** — `AuthServiceClient.get_active_superuser()` queries `auth-service`'s `GET /superuser/status`; only whoever is themselves the currently active superuser principal gets the special privileges (not every caller while some activation is running).
- **No own data storage (read side)** — `audit-service` is already the authoritative audit source; a local copy would be pure duplication (same rationale as `reporting-service`'s forensic trace). Logging (concept point 5, "complete logging") runs exclusively via a self-published `query.executed` event (exact self-audit pattern as `reporting.forensic_trace.queried`), reaching `audit-service`'s chain via the new `"query.>"` subject.
- **Manipulation scope deliberately limited to structured, curated actions** (since P8-S2, confirmed via `AskUserQuestion`) — concept 6.1's own example ("reset attribute Y on all documents of type Z with condition B") presupposes a filter-based bulk write that no owner service offers (only explicit ID-based endpoints exist anywhere in the system). A true generic SQL manipulation system would be its own multi-session project. Instead: a small, hardcoded catalog of three actions in `manipulation.py` (`document.attribute_reset`, `permission.role_assignment.delete`, `object_type.update`), each targeting a single object by ID via an already-existing owner service endpoint. Free-form SQL manipulation text remains a later extension, like the read side.
- **Protection switch as its own, lighter mechanism instead of reusing the superuser break-glass** — concept 6.1 calls it only "comparable" to 4.6, not identical. A separate, fine-grained permission `admin.query_console.manipulate` (separate from the read permission `admin.query_console`), lazy expiration check without a poll loop (an expired protection switch only blocks the next write attempt, there is nothing to clean up — unlike break-glass, whose expiration must deactivate a Keycloak account). The activated superuser bypasses the protection switch entirely ("can read and write without restriction"), does not need to activate it separately.
- **Criticality marking hardcoded, not configurable** — `ManipulationAction.is_critical` is a Python constant per action, not a database/API setting. Literal implementation of concept point 4 ("cannot be circumvented by a deviating general configuration"): safest when there is no configuration knob for it at all.
- **Four-eyes fully via the existing ADR 0022 infrastructure** (`permission-service`, P6-S4) — no parallel structure. For non-critical actions, `execute` queries `GET /approval-config/{action_type}` (an installation can configure it via the already-existing `PUT /approval-config/{action_type}`); critical actions always force an approval request, regardless of the configuration value — even for the activated superuser (the one place where the superuser does not act unrestricted). Execution after approval runs via a new NATS consumer on `permission.approval.approved` (query-service had only a producer bus until P8-S2), identical pattern to `document-service`/`auth-service`.
- **Dry-run token deliberately stateless instead of a DB table** — an HMAC-signed, short-lived token (`dry_run_tokens.py`, `DMS_DRY_RUN_SECRET`) carries `action_type`/`params`/`principal_id`/expiry itself; `/manipulate/execute` only verifies the signature, no second table needed. The token is **only checked when creating the approval request**, not again during the later asynchronous execution (approvals can stay pending arbitrarily long, exactly as with every existing ADR 0022 action).

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/query/events?actor=&subject=&event_type=&since=&until=&limit=` | Structured filter API, no parser plugin needed. Role-gated (`admin.query_console` or activated superuser), result RBAC/scope-lock filtered. Response: `{events, total_before_filter, total_after_filter, superuser}` |
| `POST` | `/query` `{query_text}` | Free-form, psql-like query text — `501` as long as no parser plugin is configured (ADR 0031); otherwise `400` on invalid text/unknown table, otherwise the same filter/audit pipeline as above |
| `POST` | `/manipulation-mode/activate` `{duration_minutes}` | Activate the protection switch (since P8-S2) — requires `admin.query_console.manipulate` or superuser |
| `POST` | `/manipulation-mode/deactivate` | Deactivate the protection switch |
| `GET` | `/manipulation-mode/status` | `{active, activated_by, expires_at}` |
| `POST` | `/manipulate/dry-run` `{action_type, params}` | Simulates a manipulation action (mandatory, even for the superuser), returns `{preview, is_critical, dry_run_token}` |
| `POST` | `/manipulate/execute` `{dry_run_token}` | Executes the action previously checked via dry run — immediately (`{status: "executed", result}`) or as an approval request (`{status: "pending_approval", approval_request_id}`), depending on criticality/configuration |
| `GET` | `/healthz` | Health check |

`X-DMS-Principal` is injected by the gateway from the bearer token (no own JWT check needed, same trust model as all other backend services). **Since Post-Roadmap Phase 38 Session 2**: `AuditClient.list_events` requires a `principal_id` kwarg, forwarded from `_run_query`'s own already-authenticated `x_dms_principal` — needed once `audit-service`'s `GET /events` itself started requiring `X-DMS-Principal` + `audit.read` (see `docs/services/audit-service.md`).

## Manipulation Mode (6.1, since P8-S2)

Curated action catalog (`manipulation.py`), each action defines `dry_run(params) -> preview text` and `execute(params) -> result`:

| `action_type` | Critical | Owner Endpoint | Concept Category |
|---|---|---|---|
| `document.attribute_reset` | No | `PATCH /documents/{id}` (document-service) | — (regular document content) |
| `permission.role_assignment.delete` | Yes | `DELETE /role-assignments/{id}` (permission-service) | "permission/role tables" |
| `object_type.update` (only `naming_constraints`/`conditions`) | Yes | `PUT /object-types/{id}` (object-type-service) | "object type/constraint definitions" |

"License data" (the third category named in the concept) does not exist — no License Service before Phase 9.

**Since Post-Roadmap Phase 38 Session 4** ([ADR 0149](../adr/0149-teamspace-permission-anchoring-broad-rbac-retrofit.md)): `clients.py`'s `DocumentClient` (used by `filtering.py`'s `GET /documents/{id}` resolution above and by the `document.attribute_reset` action's `PATCH /documents/{id}`) sends a fixed `X-DMS-Principal: query-service` header — both endpoints previously had no permission check at all.

**Authorization (Post-Roadmap Phase 38 Session 3)**: `object-type-service`'s `PUT /object-types/{id}` started requiring `admin.object_config` in that same session — `ObjectTypeClient` (`clients.py`) asserts a fixed `X-DMS-Principal: query-service` identity (same pattern as `config-service`'s `WorkflowServiceClient`/`ObjectTypeServiceClient`), granted `admin.object_config` via a one-time role assignment (not self-seeding, same "not self-healing" caveat noted on `docs/services/object-type-service.md`). `execute`/`dry_run` have no per-request human principal available to forward through the generic `ManipulationAction` interface — the mandatory four-eyes principle on this action already authorizes the actual human via the approval flow, not this HTTP call. Found via this session's own full backend-suite live run, not by an existing test (`test_manipulation.py` exercises this action only against a fake `ObjectTypeClient`, never the real HTTP call).

**Flow**: `POST /manipulation-mode/activate` (superuser exempt) → `POST /manipulate/dry-run` (mandatory for everyone, returns preview + token) → `POST /manipulate/execute` with the token → depending on criticality/configuration, immediate execution or `pending_approval` → on approval (`POST /approval-requests/{id}/approve` on `permission-service`) query-service's new consumer executes the action and publishes `query.manipulation.executed`.

The action parameters (`params`) visible in the preview text are object-specific: `document.attribute_reset` = `{document_id, attribute_key}`; `permission.role_assignment.delete` = `{role_assignment_id}`; `object_type.update` = `{object_type_id, field, value}` (`field` ∈ `naming_constraints`\|`conditions`, otherwise `400`).

## Data Model

- `manipulation_mode_status` (singleton, `id=1`): `active`, `activated_by`, `expires_at`, `updated_at` — same pattern as `permission-service`'s `SystemMaintenanceMode`.

## Events

**Consumed** (new consumer bus since P8-S2, `durable="query-service"`): `permission.approval.approved`, filtered to the three known `action_type` strings — executes the action, publishes `query.manipulation.executed`/`query.manipulation.execution_failed`. Payload read defensively (`.get()`), same ADR 0022 convention as `document-service`.

**Published** (producer bus, stream `query`):

| event_type | payload |
|---|---|
| `query.executed` | `{source: "structured"\|"sql", params, total_before_filter, total_after_filter}` — every executed query, `actor` = executing principal (concept point 5, unconditional, cannot be disabled) |
| `query.manipulation.executed` | `{action_type, params, result}` (since P8-S2) |
| `query.manipulation.execution_failed` | `{action_type, params}` (since P8-S2, e.g. when the target object was already removed elsewhere between approval and execution) |

**`audit-service` integration**: `audit_service/settings.py`'s `subjects` list extended with `"query.>"` — without this addition, `query.executed` would never reach the audit trail (the same type of error that was actually found for `"folder.>"` during the P7-S2 live test, proactively avoided here).

## Self-Registration (Concept 3.2a)

Registers itself with the registry at startup (`libs/dms-registry-client`), identical pattern to every other service. `gateway-service` routes dynamically via `service_type` (`InstanceResolver`) — no gateway code change needed to make `query-service` reachable under `/api/query-service/...`.

## Tests

`uv run pytest services/query-service/tests`: `test_parser.py`, `test_filtering.py`, `test_api.py` (from P8-S1, see above), since P8-S2 additionally `test_dry_run_tokens.py` (issuing/decoding, wrong secret, tampered payload, expired token), `test_manipulation_mode.py` (activate/deactivate/expiry), `test_manipulation.py` (all three actions: dry-run preview, execute, object type field whitelist), `test_consumer.py` (approval event triggers execution, unknown `action_type` ignored, defensive payload reading, failure case publishes `execution_failed`), `test_api.py` extended (protection switch gate, dry-run→execute with/without configured four-eyes, **critical action forces four-eyes even for the activated superuser** — the central security test of this session, invalid dry-run token). **54 tests** (previously 24, 30 new), all green, `ruff check`/`ruff format` clean.

## Open Points

- **Only `events` (audit-service) as a data source** — reporting/monitoring read models (also named by 6.1) are additive to retrofit, but not part of this session.
- **RBAC filtering only covers document/folder events** (see above) — all other categories are invisible fail-closed for non-superusers, no generic mechanism for arbitrary domains.
- ~~**`reporting-service`'s forensic trace has NO row-level result filtering like `filtering.py` here**~~ **Closed in Post-Roadmap Phase 36 Session 3**: `reporting-service` now has its own `filtering.py` with the same resource-resolution logic (`document-service` event → `document_id` → `folder_id`; `folder-service` event → `resource_id` directly; every other `service_name` hidden fail-closed) plus superuser-bypass parity, applied before anomaly detection so an unreadable event can't leak via its own anomaly hint. See `docs/services/reporting-service.md` "Row-Level RBAC Filtering".
- **No free-text SQL manipulation** — only the curated, hardcoded action catalog (see above); a true filter-based SQL manipulation system would need new bulk endpoints in several owner services, not part of this session.
- **Admin UI integration of the manipulation side completed since P8-S2b** — `ManipulationSection` in `apps/admin-ui`, see `docs/services/admin-ui.md` "Query Console". Since **P8-S3** additionally `dms query ...` in the CLI tool (6.2), the same endpoints, see `docs/tools/cli.md`.
- ~~**No reject button in the Admin UI**~~ **Closed in Post-Roadmap Phase 36 Session 3**: `QueryConsoleView`'s pending-approvals table now offers both Approve and Reject (inline reason form, same pattern as `reviewer-ui`'s `ApprovalList.tsx`), calling the already-existing `POST /approval-requests/{id}/reject`.
- **`object_type.update` only for two fields** (`naming_constraints`/`conditions`) — a deliberate whitelist, no arbitrary `ObjectType` field manipulation via this action.
