# permission-service

**Responsibility:** RBAC — roles, assignments to principals (users/groups) on resources, inheritance along a resource hierarchy, a materialized event-driven permissions cache (Concept 4.1). Since P3-S4, additionally scope locks (4.7): temporary, RBAC-overriding locking of entire resource subtrees. Since P6-S4, additionally the generic four-eyes approval mechanism (4.3), also used by other services (e.g. Document Service). Since P6-S5, additionally home of the system's own, domain-separated admin roles (4.6) — completely separate from Keycloak realm roles, see [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md). Since P6-S6, additionally home of the system-wide maintenance-mode state (emergency shutdown, 4.8) — see [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md).

**Concept reference:** 4.1, 4.3, 4.6, 4.7, 4.8, 4.4a (deputizing during absence, since P14-S11)
**Own Postgres schema:** `permission` (tables `role`, `role_assignment`, `resource_node`, `effective_permission_cache`, `scope_lock`, `approval_action_config`, `approval_request`, `system_maintenance_mode`, `delegation`, `group`, `group_membership`)

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/roles` | Create a role — since P19-S6 gated: `X-DMS-Principal` must hold `admin.user_management` ([ADR 0071](../adr/0071-permission-service-self-gating.md)), otherwise `401`/`403` |
| `GET` | `/roles` | All roles — deliberately still ungated, see ADR 0071 "Rationale" |
| `PUT` | `/roles/{role_id}` | Update description/permissions (`name` immutable) — since P12-S3, the basis for `config-service`'s role upsert by name (7.3); since P19-S6 also gated by `admin.user_management` |
| `POST` | `/groups` | Create a group (Post-Roadmap Phase 22 Session 1) — gated by `admin.user_management` like `POST /roles` |
| `GET` | `/groups` | All groups — deliberately still ungated, same rationale as `GET /roles` |
| `DELETE` | `/groups/{id}` | Delete a group including memberships (`404` on unknown ID) — gated |
| `GET` | `/groups/{id}/members` | List a group's members — ungated |
| `POST` | `/groups/{id}/members` | Add a member (`principal_id`) — idempotent, `404` on unknown group, gated |
| `DELETE` | `/groups/{id}/members/{principal_id}` | Remove a member — `404` if no membership exists, gated |
| `POST` | `/role-assignments` | Create an assignment — response `{status: "created"\|"pending_approval", role_assignment, approval_request_id}` (since P17-S3, `permission.role_assignment.create`, see below), `404` on unknown role/resource |
| `GET` | `/role-assignments?principal_id=...&resource_id=...` | List assignments, optionally filtered (since P4-S3, the basis for the Admin UI) |
| `DELETE` | `/role-assignments/{id}` | Remove an assignment |
| `GET` | `/resources/{id}` | Read a resource node |
| `PATCH` | `/resources/{id}` | Toggle `inherit` |
| `GET` | `/effective-permissions/{principal_id}/{resource_id}` | Cached effective roles/permissions |
| `GET` | `/check?...&access_type=read\|write` | Authorization check including scope-lock overlay |
| `POST` | `/check/batch` | Batch form of `/check` (since P5-S4, Search Service) — multiple `resource_ids` in a single call |
| `POST` | `/scope-locks` | Set a scope lock — response `{status: "created"\|"pending_approval", scope_lock, approval_request_id}` (since P6-S4, see below), `404` on unknown resource. Since P19-S6 gated: `locked_by` must hold `admin.user_management` (ADR 0071), otherwise `403` — the check runs before the four-eyes branch |
| `DELETE` | `/scope-locks/{id}` | Lift a scope lock (`released_by`) — same response contract (`status: "released"\|"pending_approval"`); since P19-S6 likewise gated by `admin.user_management` via `released_by` |
| `GET` | `/scope-locks?resource_id=` | List locks (optionally filtered) |
| `GET` | `/scope-locks/effective/{resource_id}` | Active locks affecting this resource (including inherited ones) |
| `GET` | `/approval-config` | All configured action types (4.3, since P6-S4) |
| `GET` | `/approval-config/{action_type}` | Configuration for an action type — default `requires_approval=false` if never set |
| `PUT` | `/approval-config/{action_type}` | Set the approval requirement for an action type (upsert) |
| `POST` | `/approval-requests` | Create an approval request (`action_type`, `initiated_by`, `payload`) — `403` if the action type has a `required_permission` (since P6-S5) that `initiated_by` does not hold |
| `GET` | `/approval-requests?status=&action_type=` | List requests, optionally filtered |
| `GET` | `/approval-requests/{id}` | Request detail (`404`) |
| `POST` | `/approval-requests/{id}/approve` | Approve (`approved_by`) — `403` if identical to `initiated_by` **or** if the `required_permission` configured for the action type is missing (since P6-S5), `409` if already decided |
| `POST` | `/approval-requests/{id}/reject` | Reject (`rejected_by`, optional `reason`) — `409` if already decided |
| `GET` | `/maintenance-mode` | Maintenance-mode status (4.8, since P6-S6): `{active, reason, triggered_by, activated_at, lifted_by, lifted_at}` |
| `POST` | `/maintenance-mode/trigger` | Trigger (`triggered_by`, optional `reason`) — always checks the `system.not_shutdown.trigger` capability on `triggered_by` first (`403` otherwise), then either direct activation or four-eyes approval depending on `approval-config`; response `{status: "activated"\|"pending_approval", maintenance_mode, approval_request_id}` |
| `POST` | `/maintenance-mode/lift` | Lift (`lifted_by`) — `403` if `lifted_by` does not match the currently active superuser (cross-service check against `auth-service`, see below) |
| `GET` | `/healthz` | Health check |
| `POST` | `/delegations` | Record a deputizing arrangement (4.4a, since P14-S11) — `delegator_principal_id` is always `X-DMS-Principal` (`401` without the header), `400` if `ends_at <= starts_at`. Publishes `permission.delegation.created` |
| `GET` | `/delegations?delegator_principal_id=&deputy_principal_id=&active_only=` | List delegations, optionally filtered |
| `GET` | `/delegations/active-for-deputy/{principal_id}` | For whom `principal_id` is currently recorded as an active deputy — the basis for the "on behalf of" selection in `reviewer-ui`/`user-ui` |
| `GET` | `/delegations/check?deputy_principal_id=&delegator_principal_id=&process_definition_id=&object_type_id=&folder_resource_id=` | The actual enforcement endpoint (same response format as `/check`) — called by `workflow-service` on task completion "on behalf of" |
| `DELETE` | `/delegations/{id}` | Early revocation — only the deputized person or `X-DMS-Roles: dms-admin` (configurable, `delegation_revoke_admin_role`), `404` on unknown ID, idempotent. Publishes `permission.delegation.revoked` |

## Data Model

- `resource_node`: `resource_id` (PK), `parent_id` (self-FK, nullable), `resource_type`, `inherit` (bool). The root node `"root"` is created idempotently on startup.
- `role`: `id`, `name` (unique), `description`, `permissions` (JSON list of capability strings, e.g. `["read","write"]`).
- `role_assignment`: `principal_type` (`user`|`group`), `principal_id`, `role_id`, `resource_id` — unique on the combination. `principal_type="group"` was, until Phase 19 Session 2, pure schema decoration (never evaluated anywhere) — since that session, exactly one reserved value (`principal_id="everyone"`) is actually processed, see "'everyone' group" below.
- `effective_permission_cache`: `(principal_id, resource_id)` → `roles`, `permissions`, `computed_at`. Is **fully cleared** on every permission/structure change (a deliberate simplification, see README) rather than being invalidated granularly per subtree.
- `scope_lock`: `id` (PK), `resource_id` (FK), `locked_by`, `reason`, `blocks_read` (bool, default `false`), `expires_at` (nullable), `created_at`, `released_at`/`released_by` (nullable) — never hard-deleted; the lifting is documented rather than removing the row (the audit trail remains complete).
- `approval_action_config` (4.3, since P6-S4): `action_type` (PK, free-form string), `requires_approval` (bool, default `false`), `required_permission` (nullable string, since **P6-S5**, 4.6), `updated_at`. If a row is missing for an action type, `requires_approval=false`/`required_permission=null` applies implicitly (a transient default object, not persisted).
- `approval_request` (4.3, since P6-S4): `id` (UUID str), `action_type`, `initiated_by`, `payload` (JSON — enough information to execute the action later), `status` (`pending`|`approved`|`rejected`), `approved_by`/`rejected_by`/`reason` (nullable), `created_at`, `decided_at` (nullable).
- `system_maintenance_mode` (4.8, since P6-S6): a singleton (`id=1`, fixed, same pattern as `OcrConfig`/`GuardConfig`), `active` (bool), `reason` (nullable), `triggered_by` (nullable), `activated_at` (nullable), `lifted_by`/`lifted_at` (nullable) — on reactivation after a lift, `lifted_by`/`lifted_at` are reset.
- `delegation` (4.4a, since P14-S11): `id` (UUID str, PK), `delegator_principal_id`/`deputy_principal_id`, `starts_at`/`ends_at` (both required), `scope_object_type_ids`/`scope_process_definition_ids`/`scope_folder_resource_ids` (each a JSON list, `null` = unrestricted on that dimension), `created_at`, `revoked_at`/`revoked_by` (nullable) — never hard-deleted, same pattern as `scope_lock` above.
- `group`/`group_membership` (Post-Roadmap Phase 22 Session 2): `group` — `id` (UUID str, PK), `name` (unique), `description`, `created_at`. `group_membership` — `id` (PK), `group_id` (FK), `principal_id`, unique on `(group_id, principal_id)`. See "Admin-Creatable Groups" below.

## Scope Locks (4.7, since P3-S4)

- A scope lock always applies to the **entire subtree** starting at `resource_id` — regardless of the individual node's `inherit` flag, which controls RBAC inheritance exclusively (see `repository.get_active_scope_locks_for_resource`, which walks the same ancestor chain as permission evaluation, but without stopping at an `inherit=false` node).
- `blocks_read=false` (default) blocks only write access, `blocks_read=true` additionally blocks read access. `GET /check` expects an explicit `access_type` parameter for this (`read`|`write`, default `write`) — the calling service must specify which kind of access is being checked.
- **Overlays RBAC rather than modifying it**: an active, blocking lock results in `allowed=false` regardless of the actually assigned permissions. Exception: principals with the `scope_lock.bypass` capability (granted via a normal role assignment) bypass the lock — after it is lifted, the original permissions apply immediately again, without anything having had to be manually revoked/reassigned.
- **Clear feedback instead of a generic error**: on a blocking lock, `CheckResult` additionally returns `blocked_by_scope_lock`, `scope_lock_reason`, and `scope_lock_expires_at`, so calling services (future API gateway/UI) can display a reason and expected duration instead of an unspecific "no permission".
- **Who may set/lift locks remains unenforced**: the endpoints themselves are ungated (analogous to the force-unlock precedent in the Document Service, P3-S2). The API gateway that has existed since P4-S1 (3.5) only checks that a valid bearer token is present at all, not whether the principal is authorized to lock — real authorization ("only admin roles may lock") would require evaluating the identity headers forwarded by the gateway within this service itself. Since **P6-S4**, a four-eyes principle can optionally be activated (see below) — this does not replace a role check, but adds a second person to the approval flow.
- **Auditing**: `POST /scope-locks` and `DELETE /scope-locks/{id}` publish `permission.scope_lock.created`/`.released` on immediate execution via a dedicated producer client (separate from the pure structure consumer, see below) — the Audit Service has additionally consumed `permission.>` since P3-S4. On approval-gated execution (see below), the same events are only published after approval, by `approval_consumer.py`.

## Four-Eyes Approval Mechanism (4.3, since P6-S4)

A generic, per-action-type configurable approval mechanism — see [ADR 0022](../adr/0022-four-eyes-approval-via-events.md) for the full architectural decision. In short:

- **Configuration per action type** (`approval_action_config`): without an explicit `PUT /approval-config/{action_type} {"requires_approval": true}`, every action remains ungated (default `false`) — "configurable per action type, not globally enforced" implemented literally.
- **Flow when approval is enabled**: the gated endpoint (here: `POST`/`DELETE /scope-locks`, externally: `document-service`'s force unlock) creates an `ApprovalRequest` instead of executing directly (`status="pending"`) and publishes `permission.approval.requested`. A second person calls `POST /approval-requests/{id}/approve` (`403` if `approved_by == initiated_by`) — this publishes `permission.approval.approved` with the original `payload`. **The actual action is not executed here**, but by a consumer of this event.
- **Self-consumption for its own action types**: `permission-service` itself consumes `permission.approval.approved` (`approval_consumer.py`) for `permission.scope_lock.create`/`.release` — exactly the same mechanism as for a foreign service, no special handling in the `approve` handler. `document-service` consumes the same event for `document.force_unlock` (its very first consumer ever, see `docs/services/document-service.md`). Since **P8-S2**, `query-service` also consumes the same event for its three manipulation action types (`document.attribute_reset`, `permission.role_assignment.delete`, `object_type.update`), see `docs/services/query-service.md`.
- **Also usable via the CLI tool** (since P8-S3, 6.2) — `dms role list/assignment ...` (`/roles`, `/role-assignments`) and `dms query approvals list/approve` (`/approval-requests`) talk to the same endpoints as Admin UI/web clients, see `docs/tools/cli.md`.
- **No execution feedback channel**: an externally executed but failed action (e.g. the lock already lifted elsewhere in the meantime) remains logged at the executing service, `ApprovalRequest.status` stays `"approved"` — see ADR 0022 "Consequences".
- **Permission/role changes wired up since P17-S3**: `POST /role-assignments` checks `permission.role_assignment.create` via the same mechanism (14.2 "permission change", one of the three action types gated by default by the eGov package, see `packages/egov/`) — response `RoleAssignmentActionResult` (`status`/`role_assignment`/`approval_request_id`), the same envelope pattern as with scope locks. For lack of its own requester field on `RoleAssignmentCreate`, `initiated_by` is the `principal_id` (the person who is to receive the role) — the same compromise as with the scope locks. Approved assignments are carried out in the same self-consumption branch of `approval_consumer.py` as scope locks/emergency shutdown.
- **`required_permission` (4.6, since P6-S5)**: a generic extension of `ApprovalActionConfig` — if set, both `initiated_by` and `approved_by` must hold this capability per `GET /effective-permissions/.../root` (in addition to the initiator≠approver rule), otherwise `403` (`MissingRequiredPermissionError`). Fixed to `breakglass.approve` at startup for `auth.superuser.activate` (superuser break-glass, see below and `docs/services/auth-service.md`) — a stricter implementation of "two different members of a permission group" (4.6) than the mere "any second person" from 4.3. Remains `null` for scope locks/force unlock, unchanged behavior.

## Domain-Separated Admin Roles (4.6, since P6-S5)

Native to the system (not Keycloak realm roles) — full architectural rationale in [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md). `repository.ensure_domain_admin_roles` idempotently seeds all `DOMAIN_ADMIN_ROLES` rows on every startup (if not already present, checked by name):

| Role | Capability | Associated technical account |
|---|---|---|
| `domain-admin-users` | `admin.user_management` | `users-admin` (created since P6-S5, `auth-service`) |
| `domain-admin-config` | `admin.object_config` | `config-admin` (created since **P6-S6**, `auth-service`) |
| `domain-admin-storage` | `admin.storage` | none yet |
| `domain-admin-license` | `admin.license` | none (enforcement directly in `license-service`, since **P9-S1**, see below) |
| `domain-admin-query-console` | `admin.query_console` | none (enforcement directly in `query-service`, since **P8-S1**, see below) |
| `domain-admin-query-console-manipulate` | `admin.query_console.manipulate` | none (enforcement directly in `query-service`, since **P8-S2**) |
| `domain-admin-deletion` | `admin.deletion` | none yet |
| `domain-admin-deletion-vs` | `admin.deletion_classified` | none yet |
| `breakglass-approver` | `breakglass.approve` | none (real humans, manually assigned) |
| `domain-admin-emergency` | `system.not_shutdown.trigger` | none (since **P6-S6**, real humans, manually assigned — see "Emergency Shutdown" below) |
| `domain-admin-virus-scan` | `admin.quarantine` | none (since **Post-Roadmap Phase 19 Session 8**, [ADR 0073](../adr/0073-ocr-rendering-virus-scan-rbac.md) — enforcement directly in `virus-scan-service`, replacing its previous plain `X-DMS-Roles` gate) |
| `domain-admin-legal-hold` | `admin.legal_hold` | none (since **Post-Roadmap Phase 19 Session 10**, [ADR 0075](../adr/0075-legal-hold-rbac.md) — enforcement in `document-service`/`folder-service`, `user-ui`'s `RetentionPanel`/`FolderRetentionModal` show/hide the action button accordingly) |
| `domain-admin-teamspaces` | `admin.teamspace_management` | none (since **Post-Roadmap Phase 22 Session 5**, [ADR 0090](../adr/0090-teamspaces-admin-overview.md) — enforcement directly in `teamspace-service`'s new `GET /admin/teamspaces`, `admin-ui`'s new `/teamspaces/` page) |
| `domain-admin-classification` | `admin.classification` | none (since **Post-Roadmap Phase 31 Session 3**, [ADR 0114](../adr/0114-per-document-classification-level.md) — enforcement in `document-service`'s new `PUT .../classification-level`, deliberately separate from `admin.object_config`; `user-ui`'s new `ClassificationPanel` shows/hides the raise action accordingly) |

`domain-admin-users` and (since **P6-S6**) `domain-admin-config` have their own technical Keycloak account (`auth-service`'s `/users` or `workflow-service`'s process-definition endpoints, Admin UI gating). `domain-admin-query-console`/`-manipulate` (since **P8-S1**/**P8-S2**) as well as, since **P9-S1**, `domain-admin-license` are likewise actually enforced, but **without** their own technical account — the respective service checks the role assignment directly via `GET /effective-permissions/{principal}/root`, no dedicated account needed (see `docs/services/query-service.md`/`docs/services/license-service.md`). The remaining ones are predefined ("shipped by default", 4.6), but without an account/enforcement. `breakglass-approver` and (since P6-S6) `domain-admin-emergency` deliberately get no automatic account — the four-eyes rule from 4.6, or the trigger permission from 4.8, requires a real, individually attributable person, not a shared technical identity; assignment to actual humans happens via the existing, self-gated `POST /role-assignments` usage in the Admin UI.

## Emergency Shutdown (4.8, since P6-S6)

System-wide emergency lock + maintenance mode — full architectural rationale in [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md). In short:

- **Trigger** (`POST /maintenance-mode/trigger`): **always** directly checks the `system.not_shutdown.trigger` capability on `triggered_by` via a new `repository.require_capability()` function extracted from `_require_permission_if_configured` — unlike the previous four-eyes cases (scope locks, break-glass), there is here a baseline permission check even **without** the four-eyes principle activated, since 4.8 literally requires "freely configurable who may trigger it". If `ApprovalActionConfig("system.not_shutdown.trigger").requires_approval` is set (default `false`, **not** hardcoded to `true` like break-glass), the actual activation instead runs through the existing four-eyes mechanism (P6-S4).
- **Lift** (`POST /maintenance-mode/lift`): only the currently active superuser (4.6) may lift it — checked via a new `auth_client.py` (`GET /superuser/status` on the Auth Service, extended by `principal_id`). `403` if no superuser is active or `lifted_by` does not match their `principal_id`. **The Permission Service's first cross-service call in this direction** (the Auth Service has already called the Permission Service since P6-S5) — deliberately no Docker Compose `depends_on` for this, since the call is at request time, not at its own startup (see ADR 0024).
- **Self-consumption**: with approval enabled, this service consumes `permission.approval.approved` for `action_type="system.not_shutdown.trigger"` itself (a third branch in `approval_consumer.py`, same principle as scope locks).
- **The lock is not enforced here but by the gateway** (`docs/services/gateway-service.md`) — this service only delivers the state (`GET /maintenance-mode`) and the activation/lifting logic.

## Batch Check (since P5-S4, Search Service)

`POST /check/batch` (body: `{principal_id, permission, access_type, resource_ids: [...]}`, response: `{results: {resource_id: bool}}`) was added for the new Search Service: a search result list can involve many different folders, and the existing `GET /check` only checks one principal/resource/permission triple per call — many individual roundtrips would be impractical for a results page. The implementation repeats exactly the same logic as `/check` (including the scope-lock overlay) in a loop over the (deduplicated) `resource_ids` — each pass hits the already existing `effective_permission_cache`, so there is no expensive recomputation despite the loop; a mega SQL query would be overengineering for the scope relevant here (search result pages, not bulk operations). Search Service uses folder `resource_id`s (not document IDs) as the object to check — documents are not themselves permission resources, see `docs/services/search-service.md`.

## Inheritance Algorithm

Starting from the requested resource, the ancestor chain (`parent_id`) is walked upward, and the principal's assignments are collected at every node. A node with `inherit=false` ends the walk-up **after** evaluating its own assignments — standard DMS behavior (SharePoint/Alfresco), as required by Concept 4.1.

## "everyone" Group (Post-Roadmap Phase 19, since Session 2, see [ADR 0067](../adr/0067-everyone-gruppe-permission-service.md))

`principal_type="group"` was pure schema decoration until this session — `_collect_effective_roles` only checked
`RoleAssignment.principal_id == principal_id`. Since Session 2, at every traversed
resource node it additionally checks whether an assignment with `principal_type="group",
principal_id="everyone"` exists — **every** authenticated principal is thereby implicitly considered a
member, regardless of their own `principal_id`. The inheritance algorithm itself (ancestor chain,
`inherit=false` stops the walk-up) applies identically to "everyone" assignments as to individual assignments.

- **`repository.ensure_everyone_role`** (bootstrap, lifespan, the same idempotent pattern as
  `ensure_domain_admin_roles`) creates both the `Role("everyone")` and its `RoleAssignment` at the
  root resource — unlike domain admin roles, "everyone" has no external account to which the
  assignment could otherwise be attributed.
- **Currently seeded permissions**: `users.lookup`, `users.directory` — these correspond to the two endpoints
  hardcoded as open in `auth-service` since P14-S6/P15-S4 (`GET /users/lookup`, `GET
  /users/directory`, previously without any RBAC check). **This session does not yet change `auth-service` itself**
  — the actual conversion of the two endpoints to a real `has_permission` check follows
  in P19-S3. **Since P19-S5** ([ADR 0070](../adr/0070-case-service-rbac.md)) additionally `case.read`/
  `case.write` — `case-service` previously had no permission check at all; the extension preserves the
  previous de-facto-open behavior. **Since P19-S7** ([ADR 0072](../adr/0072-archival-reporting-rbac.md))
  additionally `archival.read`, `archival.write`, `reporting.read`, `reporting.write`,
  `reporting.forensic_trace` — the same principle for `archival-service`/`reporting-service`. **Since P19-S8**
  ([ADR 0073](../adr/0073-ocr-rendering-virus-scan-rbac.md)) additionally `ocr.read`, `ocr.write`,
  `rendering.read`, `rendering.write` — the same principle for `ocr-service`/`rendering-service`.
  **Deliberately NOT included**: `admin.quarantine` (`virus-scan-service`, the same session) — unlike
  the others, the quarantine area was already, before P19-S8, a real permission restricted to a
  dedicated role (`domain-admin-virus-scan`), not a previously de-facto-open gap. **Important for
  future extensions of this list**:
  `ensure_everyone_role` does NOT automatically update an already-created "everyone" role (no
  migration mechanism, see its docstring) — on an already running installation, a new permission
  must be manually applied once via `PUT /roles/{id}`.
- ~~No complete group management system: `"everyone"` is the only reserved
  group identifier, no custom groups with their own membership management.~~ — **partially
  fixed in Post-Roadmap Phase 22 Session 2**, see "Admin-Creatable Groups" below. Real
  AD group synchronization (concept extension "AD group → internal role mapping rule engine", **Phase
  24 Session 2**) remains a separate, still-open point — the groups added here are
  purely manually admin-maintained, no automatic reconciliation with an external directory service.

## Admin-Creatable Groups (Post-Roadmap Phase 22 Session 2)

Extends the hardcoded "everyone" group described above with real, admin-managed groups with
explicit membership. Unlike `"everyone"` (implicit membership for every authenticated
principal, no own data row), every group created via `POST /groups` needs explicit
`group_membership` rows (`POST /groups/{id}/members`) to be effective.

- **`_collect_effective_roles`** collects, once, before traversing the resource ancestor chain, all
  `group_id`s the requested `principal_id` belongs to via `group_membership` (`_group_ids_for_
  principal`), and at every node additionally treats, in addition to `principal_id == principal_id` and the
  "everyone" condition, every assignment with `principal_type="group", principal_id IN (member groups)` as
  matching. A role assignment to a group (instead of to each individual principal) thereby applies to
  every enrolled member.
- **Same self-gating as `POST`/`PUT /roles`** (ADR 0071, `admin.user_management`) for
  `POST`/`DELETE /groups` and `POST`/`DELETE /groups/{id}/members` — a group is ultimately just another
  building block of permission management. `GET /groups`/`GET /groups/{id}/members` remain deliberately
  ungated, same rationale as `GET /roles`.
- **Adding a member is idempotent** (a second `POST` with the same `principal_id` returns the
  already existing membership instead of a duplicate error) — matches the otherwise deliberately
  low-error style of this service (cf. `ensure_everyone_role`).
- **Deleting a group** also removes its `group_membership` rows, but deliberately does NOT check whether
  `RoleAssignment` rows still reference this group ID (no FK from `role_assignment` to `group`,
  `principal_id` is a free-form string there) — an orphaned assignment simply no longer matches any
  principal afterward, the same behavior as a group that was never assigned any members. Consistent with
  `Role`, which likewise has no delete endpoint/reference check.
- **Every membership/deletion change invalidates the entire `effective_permission_cache`** (the same
  coarse-grained strategy as every other permission-changing operation in this service) — a removed
  member thereby immediately loses the permissions held via the group, not only at the
  next independent cache clear.
- **Admin UI integration**: `apps/admin-ui`'s `UserManagement` page got a new section
  "Groups" (see `docs/services/admin-ui.md`).

## Structure Synchronization (Contract Confirmed Since P3-S3)

The Folder Service (P3-S3) implements exactly the contract provisionally assumed in P2-S2 — no adjustment needed. `structure_consumer.py` subscribes to `settings.structure_subjects` (default `["folder.>"]`) via `NatsEventBusClient(ensure_stream=False)`:

| event_type (suffix) | payload |
|---|---|
| `*.resource.created` | `{resource_id, parent_id, resource_type}` |
| `*.resource.moved` | `{resource_id, new_parent_id}` |
| `*.resource.deleted` | `{resource_id}` |

Verified live end-to-end (P3-S3): a folder created via the real Folder Service API immediately appears as a `ResourceNode` in this service, including the correct `parent_id`.

**Known limitation**: if no stream exists yet at startup for a configured subject (no producer has ever run), the subject is skipped (`SubjectNotFoundError` caught, see `dms-eventbus-client`/ADR 0001) instead of blocking service startup — but there is no retry loop that later picks up the stream automatically; a restart is then needed. In practice uncritical, since the Folder Service now exists and creates its stream on its own startup.

## Deputizing During Absence (4.4a, since P14-S11)

Time-limited, scope-restricted transfer of task handling from an absent person (`delegator_principal_id`) to a deputy (`deputy_principal_id`) — deliberately NOT an identity switch: the deputy continues to act under their own account, this record is only the basis for the permission check and the "on behalf of" audit note. Full rationale, in particular why delegation lives here rather than in `workflow-service` and why only `scope_process_definition_ids` is actually evaluated: [ADR 0048](../adr/0048-delegation-lives-in-permission-service-no-task-assignee-retrofit.md).

- **`POST /delegations`** is a self-service endpoint — `delegator_principal_id` always comes from `X-DMS-Principal`; no one can create a delegation on behalf of a third person.
- **`GET /delegations/check`** is the only real enforcement point — `workflow-service`'s `POST .../tasks/{id}/complete` calls it whenever a completion includes `on_behalf_of_principal_id` (see `docs/services/workflow-service.md`).
- **Revocation** (`DELETE /delegations/{id}`) only by the deputized person or `X-DMS-Roles: dms-admin` (configurable, `delegation_revoke_admin_role`) — NOT by the deputy themselves.
- `admin-ui` (`/delegations/`) offers a pure installation-wide overview + admin revocation; creation remains purely self-service (`user-ui`'s `DelegationsPane`).

## Events

**Consumes:** `folder.>` (contract confirmed, see above); since P6-S4 additionally its own `permission.approval.approved` (self-consumption, see above).
**Publishes** (stream `permission`, `ensure_stream=True`, since P3-S4):

| event_type | payload |
|---|---|
| `permission.scope_lock.created` | `{scope_lock_id, resource_id, locked_by, reason, blocks_read}` |
| `permission.scope_lock.released` | `{scope_lock_id, resource_id, released_by}` |
| `permission.approval.requested` | `{request_id, action_type, initiated_by}` (since P6-S4) |
| `permission.approval.approved` | `{request_id, action_type, initiated_by, approved_by, payload}` (since P6-S4) |
| `permission.approval.rejected` | `{request_id, action_type, initiated_by, rejected_by, reason}` (since P6-S4) |
| `permission.maintenance_mode.activated` | `{triggered_by, reason}` (since P6-S6, 4.8) |
| `permission.maintenance_mode.lifted` | `{lifted_by}` (since P6-S6, 4.8) |
| `permission.delegation.created` | `{delegation_id, deputy_principal_id}` (since P14-S11, 4.4a) |
| `permission.delegation.revoked` | `{delegation_id}` (since P14-S11, 4.4a) |

## Self-Registration (Concept 3.2a, since P4-S1)

Registers itself with the registry on startup (`libs/dms-registry-client`: register, periodic heartbeat, deregister on shutdown) — the basis for the API gateway's routing (`docs/services/gateway-service.md`). Opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; without both values the service runs unchanged, without discovery.

## Sensors (Concept 10.1)

None yet — follows in Phase 11.

## Open Points

- ~~Real, custom group membership continues to go unresolved~~ — **admin-defined groups with real membership resolution added in Post-Roadmap Phase 22 Session 2**, see "Admin-Creatable Groups" above. Still open: **automatic** AD group synchronization (the new groups are purely manually admin-maintained) — planned as a standalone **Phase 24 Session 2** ("AD group → internal role mapping rule engine").
- More granular cache invalidation (only the affected subtree instead of the entire cache) is possible as a later optimization, without changing the API.
- **`PUT /approval-config/{action_type}` remains deliberately ungated** (checked, not implemented, in **Post-Roadmap Phase 22 Session 3**, when this endpoint first got Admin UI integration, see `docs/services/admin-ui.md` "Four-Eyes Settings") — self-gating analogous to `POST`/`PUT /roles` (ADR 0071) would have touched over a dozen test suites across the repo (auth-/config-/folder-/document-/migration-/workflow-service, webdav-connector call this endpoint directly as test infrastructure, without `X-DMS-Principal`). Anyone who obtains arbitrary access to the gateway (e.g. via a compromised but otherwise unprivileged account) can thereby today toggle the four-eyes requirement for ANY action type system-wide — a real, still-open security issue for a future, dedicated hardening session.
- **The four-eyes principle (4.3) has been generically available since P6-S4, since P6-S5 also with optional role binding (`required_permission`)** — wired up for scope locks, Document Service force unlock, and superuser break-glass (`auth.superuser.activate`). Permission/role changes (`POST /role-assignments`, `POST /roles`) still do not use it, see "Four-Eyes Approval Mechanism" above.
- **A delegation's `scope_object_type_ids`/`scope_folder_resource_ids` (4.4a, since P14-S11) are currently not evaluated by any endpoint** — only `scope_process_definition_ids` is actually effective at `GET /delegations/check` (see ADR 0048). The other two fields are stored (concept wording fully represented), but would need an additional cross-service detour via `business_key` to be evaluated on a concrete task completion — not part of this session.
- ~~"I deputize for"/"On behalf of" displays (4.4a) show raw principal IDs, not usernames~~ — **fixed in Post-Roadmap Phase 19 Session 4** ([ADR 0069](../adr/0069-rueckwaerts-identitaetsaufloesung.md)): a new `GET /users/{id}` reverse identity resolution endpoint in `auth-service`, `user-ui`'s `DelegationsPane` now uses it for both lists ("My Deputizations"/"I Deputize For").
- ~~No enforcement of who may set/lift scope locks~~ — **fixed in Post-Roadmap Phase 19 Session 6** ([ADR 0071](../adr/0071-permission-service-self-gating.md)): `POST`/`DELETE /scope-locks` now check `locked_by`/`released_by` against `admin.user_management`, before the existing four-eyes branch.
- **No execution feedback channel / no approver notification** for the approval mechanism — see [ADR 0022](../adr/0022-four-eyes-approval-via-events.md) "Consequences".
- `GET /check` relies on the caller to pass the correct `access_type` (`read`/`write`) — the service itself has no fixed mapping of permission name → access type.
- ~~`POST`/`GET`/`PUT /roles` and `POST`/`GET`/`DELETE /role-assignments` remain ungated~~ — **`POST`/`PUT /roles` fixed in Post-Roadmap Phase 19 Session 6** (ADR 0071, `admin.user_management`). `GET /roles` as well as all three `/role-assignments` endpoints remain deliberately ungated: `GET /roles` is relied upon by many services for get-or-create-role-by-name, and `POST /role-assignments` would have placed `auth-service`'s bootstrap seeding call (which creates the very first role assignment) in front of the chicken-and-egg problem described in ADR 0023 (see ADR 0071 "Rationale" for confirmation that this problem does not affect `/roles` itself). The actual configuration import that calls `PUT /roles/{id}` in practice was already self-gated before (`config-service`, `admin.object_config`) — since this session it additionally holds `admin.user_management`.
- **No general superuser bypass for `require_capability`** (since Post-Roadmap Phase 19 Session 6, ADR 0071 "Rationale"): only `POST /maintenance-mode/lift` has a hardcoded superuser special check. A break-glass superuser without an explicit `admin.user_management` assignment can, since this session, no longer create/modify roles or set/lift scope locks — a larger, architectural change outside previous sessions.
- **5 of the 7 domain admin roles from 4.6 have no associated technical account** (since P6-S5/S6): see "Domain-Separated Admin Roles" above — to be addressed with the future retrofit session for the respective domain.
- **Federation Hub (7.4) and plugin instances (3.8) do not exist** (4.8, since P6-S6): maintenance mode can therefore neither "pause federation operations" nor "halt plugin instances" — both effects from 4.8 remain unimplemented, see [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md).
- **No system-wide write prohibition beyond the gateway** (4.8, since P6-S6): direct service-to-service write calls bypassing the gateway remain possible during maintenance mode, see ADR 0024.
- **No elevated audit priority for emergency-shutdown events** (4.8, since P6-S6): `AuditEvent` still has no priority field, see ADR 0023/0024.
