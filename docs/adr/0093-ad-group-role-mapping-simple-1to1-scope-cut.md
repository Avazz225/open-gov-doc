# 0093 — AD group → role mapping: dedicated lean table in auth-service, scope cut to simple 1:1 mapping

**Status:** accepted (Post-roadmap Phase 24 Session 2)
**Context:** Concept 4.4 ("AD group memberships are mapped to internal roles"), affects `auth-service`

## Decision

1. **New, dedicated table `ad_group_role_mapping`** (`auth` schema, `id`, `ad_group_name`, `role_name`,
   `created_at`, `created_by`) instead of reusing/extending `permission-service`'s
   `Group`/`GroupMembership`/`RoleAssignment` (post-roadmap Phase 22 Session 2, ADR 0088). Those model
   ADMIN-DEFINED groups with an explicit membership table that must be kept in sync — a different,
   independent function from the mapping of EXTERNAL Keycloak/AD group claims being implemented here. The
   new table deliberately lives locally in `auth-service` (small blast radius, no coupling to
   `permission-service`'s group machinery).
2. **Explicit scope restriction relative to Concept 4.4**: only simple 1:1 mapping (one
   `ad_group_name` → one `role_name`) — Concept 4.4 describes, as the full target build-out, additionally
   composite rules ("AD group X **and** attribute Y → role Z", "multiple AD groups → one shared role").
   This session explicitly implements ONLY the simple variant, no generic rule DSL — see
   "Consequences"/`docs/services/auth-service.md` "Open Points".
3. **Dynamic evaluation on every `/me` request**, no caching/no dedicated membership table: Keycloak
   already returns a principal's current group memberships fresh on every token fetch via the `groups`
   JWT claim — `ad_group_mapping.resolve_roles_for_groups` reads directly against the mapping table on
   every call, so a change/deletion of a mapping takes effect from the next `/me` call onward with no
   invalidation problem.
4. **The `groups` claim was previously entirely missing from the access token** — Keycloak does not
   automatically include group memberships (unlike roles via `realm_access.roles`). New protocol mapper
   `oidc-group-membership-mapper` (`bootstrap._ensure_groups_mapper`, `full.path=false` — just the bare
   group name, no Keycloak-internal path, matching the simple name-based mapping), runs like
   `_ensure_client_updated` on EVERY startup (not just first client setup), since `skip_exists=True` in
   `create_client` would otherwise never add the new mapper to an already-existing client.
5. **`realm_roles` in `GET /me` remains ONE field** — roles derived from the group claim are merged into
   the same list instead of a separate `group_derived_roles` field, deduplicated. See "Rationale".
6. **CRUD endpoints (`GET`/`POST`/`DELETE /ad-group-mappings`) gated on `admin.user_management`** — the
   same capability/domain as `GET /users`/`POST /realm-roles` ("user/rights management"), no new, more
   fine-grained capability for this session.
7. **Auditing via the existing event bus mechanism**: `auth.ad_group_role_mapping.created`/`.deleted`
   (`actor=` calling principal) — `audit-service` already consumes the entire `auth.>` subject
   (since P6-S5), no new audit mechanism needed. `created_by`/`created_at` additionally kept directly on
   the row for a quick look without an audit-trail query.

## Rationale

- **Why no generic rule DSL in this session**: Concept 4.4 itself introduces composite rules only as an
  example of a possible target build-out ("as well as more complex rules"), without prescribing a concrete
  rule format — a viable, admin-editable DSL for boolean group/attribute combinations is a standalone,
  substantially larger design effort (editor UI, validation, evaluation order for conflicting rules) and
  is explicitly planned as its own, later session (this task assignment itself names the scope cut). The
  simple 1:1 mapping already covers by far the most common AD use case (one AD group per
  department/role) and is fully reviewable in one session.
- **Why a new, dedicated table instead of `permission-service`'s `Group`/`GroupMembership`**: an
  admin-created `Group` (ADR 0088) needs explicit `GroupMembership` rows that would have to be kept in
  sync with an external source (AD/Keycloak) to represent the same function — that would be a
  synchronization problem (Concept 4.4 itself names a "configurable synchronization interval" for AD
  user/group reconciliation, deliberately NOT implemented here as well, see "Consequences"). The
  claim-based evaluation on every token fetch, by contrast, needs no membership table at all — Keycloak/AD
  remains the sole source of truth for "who is in which group".
- **Why `realm_roles` is merged instead of using a separate field**: every existing caller of `GET /me`
  (frontend role checks, `permission-service`'s role-assignment reconciliation) already reads
  `realm_roles` as the complete role list of a principal — an additional field would have forced EVERY
  such caller to change in order to even consider the new role source. From the rest of the system's
  perspective, a role should behave the same regardless of whether it was assigned directly as a Keycloak
  realm role or derived via a group membership.
- **Why `admin.user_management` instead of a new capability**: this session deliberately adds no new
  permission name, to keep within the blast-radius requirement — a misconfigured mapping can silently
  grant users additional roles, so it clearly belongs in the same security-relevant domain as user/rights
  management itself. A more fine-grained capability remains a later, non-blocking extension.

## Consequences

- **Composite rules (group AND attribute, multiple groups → one role) remain unimplemented** — Concept
  4.4 explicitly not fully covered, documented open point.
- **No "configurable default behavior for unmapped AD groups"** (Concept 4.4 explicitly names "no role
  granted vs. defined default role" as a configuration option) — this session implements only the first
  behavior (no role), hardcoded, no setting for it.
- **No "no live editing with immediate blast effect without a control" approval step** (Concept 4.4:
  "changes to the mapping only take effect after explicit approval/save") — this session makes every
  change effective immediately (save = approval), no additional four-eyes step as with
  `permission.role_assignment.create` (ADR 0060). Documented open point, not a deliberate security gap
  (the change itself is already gated by `admin.user_management` and audited).
- **No AD synchronization interval/no user/group synchronization** (Concept 4.4, final paragraph) — this
  session reads group memberships exclusively from the JWT `groups` claim at token-fetch time, no periodic
  reconciliation, no dedicated user/group table.
- **JSON configuration export (Concept 4.4: "part of the JSON configuration export (7.3)")** not part of
  this session — `ad_group_role_mapping` rows are currently not part of `config-service`'s configuration
  packages, so a mapping cannot be transferred between installations as envisioned in the concept.
- **`skip_exists=True` limit remains** (already documented for the audience mapper): changes to the new
  `groups` mapper itself (e.g. later `full.path=true`) would not automatically be picked up on an
  already-existing client — uncritical for dev/test, `_ensure_groups_mapper` only checks for the
  existence of a mapper named `groups`, not its configuration content.
