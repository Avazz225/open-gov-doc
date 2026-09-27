# 0233 — config-service: two new exportable categories (deletion-reason catalog, org-unit→role linking)

**Status:** accepted
**Context:** P80-S2 (Phase 80, second session — eGov configuration package, remaining structural gaps).
The eGov package (14.2) already ships object types, workflows, business calendars, roles, and approval
defaults, but two of the platform's own admin-configurable mechanisms had no exportable representation in
`config-service`'s `ConfigDocument` (7.3) at all: (a) the deletion-reason catalog (`RetentionConfig.
deletion_reason_catalog`, ADR 0112 — an admin-curated list of UX suggestions, not an enum) and (b)
linking an org-unit group (`Group.is_org_unit`, ADR 0143) to a role, so that a records-management package
can ship "every member of the `Amtsleitung` org unit gets the `Amtsleitung` role" as a config default
instead of requiring a separate manual step per installation. Without this, the package's `Abteilung`
folders (object-type category) and its admin roles (`roles` category) remained two unconnected islands.

## Decision

Add three new `ConfigDocument` categories, all following the project's existing "thin translator, no
Postgres schema of its own" shape (`config-service` already reads/writes exclusively at the respective
owner services for every other category):

1. **`deletion_reason_catalogs`** — a singleton category (like `sensor_config`/`federation_config`/
   `branding_config`), even though the underlying data is duplicated per-service today
   (`document-service` AND `folder-service` each hold their own `RetentionConfig.deletion_reason_catalog`
   row). Applied by fanning out to both owners. **Deliberately an additive merge, not a replace** — the
   only category in this service with that behavior — because a config package shipping a small set of
   common defaults must not silently delete reasons an installation already curated for itself, unlike
   e.g. `roles`/`business_calendars`, which upsert-by-name and fully overwrite the matched entry's other
   fields. Export reports the union of both services' catalogs.
2. **`org_units`** — upserts a `permission-service` `Group` with `is_org_unit=True`, upsert-by-`name`
   like `roles`. An existing group of the same name is *promoted* to an org unit if it wasn't one, never
   demoted — a package should not strip a flag an installation manages itself for an unrelated group of
   the same name.
3. **`org_unit_role_links`** — grants a role to every member of an org-unit group. **No new
   authorization/resolution mechanism was built.** `permission-service`'s `RoleAssignment` already
   supports `principal_type="group"`, and `_collect_effective_roles` already unions role assignments for
   a principal's group memberships (used since Phase 19 for the implicit `"everyone"` group). This
   category is therefore pure plumbing: resolve the named org-unit group and role by name, then call the
   existing `POST /role-assignments` with `principal_type="group", principal_id=<group.id>`. Idempotent
   (checked against `GET /role-assignments` before creating, to survive the unique constraint on
   `(principal_type, principal_id, role_id, resource_id)` across repeated imports).

Dispatch order in `apply_import` matters, in two ways:
- `roles` and `org_units` are applied before `org_unit_role_links` (same "dependency before dependent"
  convention already used for `dmn_definitions` before `workflows`), so a single import document can
  define all three together, as the eGov package now does.
- `org_units`/`org_unit_role_links` are applied **before** `approval_config` — found live, not by
  inspection: a package (like the eGov one) that sets `permission.role_assignment.create` to
  `requires_approval: true` in its own `approval_config` category would, if `approval_config` ran first,
  make its own `org_unit_role_links` entries fall under four-eyes and defer to a pending approval
  instead of actually taking effect — a self-inflicted footgun a config PACKAGE ("directly usable after
  applying it," 14.1) must not hit against itself. `apply_org_unit_role_links` additionally checks the
  actual `POST /role-assignments` response status (`"created"` vs. `"pending_approval"`) rather than
  assuming success from a 2xx status — a link genuinely deferred to four-eyes (e.g. because an
  installation had already turned that requirement on itself, independent of this package) is reported
  in `CategoryResult.errors`, not silently counted as `created`.

## Consequences

- `config-service` gained two new outbound dependencies (`document-service`, `folder-service`), via a
  new shared `RetentionConfigClient` (one class, instantiated twice — the two services' `retention-config`
  endpoints are byte-for-byte identical in shape). `config-service`'s own technical account needed a
  fourth bootstrap role, `domain-admin-retention` (`admin.retention`), added to `_REQUIRED_ROLE_NAMES`.
- `org_unit_role_links`'s compare/diff identity field (`org_unit_name`) does not uniquely distinguish two
  links sharing the same org unit but different roles — accepted, same class of tradeoff every other
  identity-field-based category in `compare.py` already makes, and the primary use case (one org unit,
  one role) is unaffected.
- The eGov package (`packages/egov/config.json`) now ships concrete defaults using both new categories —
  see its own README for what was added.
