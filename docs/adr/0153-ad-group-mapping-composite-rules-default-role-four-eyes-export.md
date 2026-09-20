# 0153 — AD group→role mapping: composite (AND) rules, configurable default role, four-eyes, config export/import

**Status:** accepted
**Context:** P39-S3 (Post-Roadmap Phase 39, concept 4.4/7.3/14.2). [ADR 0093](0093-ad-group-role-mapping-simple-1to1-scope-cut.md)
(P24-S2) built AD/Keycloak group→role mapping as a deliberately narrow 1:1 table, explicitly naming four
deferred items in its own "Consequences": (1) no composite rules ("group AND attribute, multiple groups
→ one role via AND logic"), (2) no configurable default for unmapped groups (hardcoded "no role"), (3)
no four-eyes on mapping changes ("save = approval", not treated as a security gap since already gated +
audited), (4) not part of `config-service`'s export bundle. A research pass ahead of this session
verified all four against the current code (unlike most prior sessions in this plan, where a similar
check found the named gap already stale/partially closed) — all four were confirmed still real and
accurately described, so this session builds all four directly, no scoping-only sub-deliverable needed
this time.

## Decision

**(1) Composite (AND) rules are a NEW, ADDITIVE mechanism, not a replacement of the simple table.**
Two new tables, `AdGroupRoleCompositeRule` (`id`, `role_name`, `created_at`, `created_by`) and
`AdGroupRoleCompositeRuleGroup` (`id`, `rule_id` FK, `ad_group_name`), model a rule that grants
`role_name` only if a principal's Keycloak `groups` claim is a superset of every `ad_group_name` linked
to that rule (AND logic) — as opposed to the pre-existing `AdGroupRoleMapping`'s OR-across-rows
semantics. `ad_group_mapping.resolve_roles_for_groups()` now unions BOTH mechanisms: the untouched
simple-table lookup plus a new pass over composite rules, deduplicated. A composite rule needs at least
2 distinct groups (`422` otherwise) — a 1-group "composite" rule would be a redundant duplicate of the
simple mechanism. No ORM `relationship()` is used between the two new tables (a plain FK column plus
explicit, separately-joined queries in `ad_group_mapping.py`) — matching this project's own established
convention (verified: no `models.py` anywhere in this codebase uses SQLAlchemy `relationship()`).

**(2) A configurable default role lives in a new singleton table `AdGroupMappingDefaultRole`** (`id=1`,
`default_role_name`, `updated_at`, `updated_by` — same pattern as `SsoConfig` elsewhere in this
service). `resolve_roles_for_groups()` returns the default role ONLY when the principal has at least one
AD group claim AND neither the simple table nor any composite rule matched anything — deliberately NOT
when the principal has NO AD group claim at all. A principal without any AD/Keycloak group membership is
not "unmapped" in the sense concept 4.4 and ADR 0093 describe; treating it as such would silently grant
the default role to every account with no group claim whatsoever (e.g. any purely local/non-domain
account), a materially broader and unintended blast radius than "a real AD group with no matching rule".

**(3) Four-eyes on mapping/rule changes uses a NEW, OPTIONAL, per-action-type-configurable pattern —
not the mandatory pattern already used by cross-service break-glass (`auth.superuser.activate`, ADR
0023/0024).** `PermissionServiceClient` (`auth-service`'s own client, not the generic library) gains two
new methods, `requires_approval(action_type)` (a REMOTE `GET /approval-config/{action_type}` call) and
`request_approval(...)` (a REMOTE `POST /approval-requests` call) — auth-service's first-ever remote
check of `permission-service`'s approval-config, since (unlike `permission-service`'s own gated
endpoints, e.g. `POST /roles`, ADR 0130/0151) the config lives in a different service's database. A new
shared `_maybe_defer_to_approval()` helper wraps all four mutating admin endpoints (mapping create/
delete, composite-rule create/delete): each checks remotely first, defers to `pending_approval` if
gated, executes directly otherwise — exactly mirroring the OPTIONAL, configurable philosophy already
used everywhere else in this project (ADR 0130/0151/0024's `system.not_shutdown.trigger`), not
break-glass's unconditional-mandatory pattern (which has no direct/bypass endpoint at all, functionally
hardcoding approval). AD-group-mapping changes are meaningfully less sensitive than superuser
activation, so the lighter, opt-in pattern is the appropriate fit. `auth-service`'s own NATS consumer
(`consumer.py`) gained four new branches executing the actual mutation once
`permission.approval.approved` arrives for one of these action types, publishing the same
`auth.ad_group_role_mapping.created`/`.deleted`/`auth.ad_group_role_composite_rule.created`/`.deleted`
events the direct path already published. `PUT /ad-group-mappings/default-role` is deliberately **NOT**
four-eyes-gated — the plan's "four-eyes on mapping changes" deliverable names mapping/rule CRUD
specifically (real per-item rows), and this is a single scalar setting; gating it would be scope beyond
what was asked, not a gap this session leaves open by oversight.

**Response shape changes, matching ADR 0151's identical precedent**: `POST /ad-group-mappings` changes
from a bare `AdGroupRoleMappingOut` to `AdGroupRoleMappingActionResult`
(`status`/`mapping`/`approval_request_id`); `DELETE /ad-group-mappings/{id}` changes from `204 No
Content` to `200` with `AdGroupMappingApprovalStatus`. Verified via research ahead of this session: **no
frontend caller of this endpoint exists anywhere in this project** (confirmed via exhaustive grep across
all six frontend apps) — this is, and remains, a backend-only, API/curl-tested feature, so this breaking
change has zero actual blast radius today, unlike ADR 0151's `PUT /roles/{id}` change (which had one
known, verified-unaffected caller).

**(4) `config-service` gains a new `ad_group_mappings` export/import category.** New auth-service
endpoints `GET`/`POST /ad-group-mapping-config`(`/import`) bundle all three mechanisms (simple mappings,
composite rules, default role) in one payload, gated via `X-DMS-Principal`/
`_require_service_user_management` — the SAME service-to-service auth mechanism as the pre-existing
`GET`/`POST /realm-roles` (header-based), deliberately NOT the bearer-token
(`Depends(get_current_user)`) admin CRUD surface above (`config-service` has no bearer token to present,
same reasoning as every other service-to-service caller in this project). Import is idempotent per item
(`ad_group_mapping.mapping_exists`/`composite_rule_exists` skip an exact existing row instead of
erroring, mirroring `POST /realm-roles`'s own `skip_exists=True`) and unconditionally overwrites
`default_role_name` (including resetting it to `null`, since the category is only ever passed to the
endpoint when actually present in the import package — see the code comment at the call site). Import
deliberately does **not** go through the four-eyes checks from (3) above — the same pre-existing
precedent as `POST /realm-roles` (also not four-eyes-gated), not a new gap this session introduces; a
config-import package is typically already reviewed at the exporting environment before promotion.
`admin-ui`'s `CONFIG_CATEGORIES` constant (`apps/admin-ui/src/lib/api.ts`) gained the new category name -
the only frontend touch in this whole session, since the export/import page renders categories
generically with no per-category logic to add.

**No admin UI for the mapping/composite-rule/default-role CRUD itself was built.** Verified ahead of this
session: no such UI existed before (backend-only since ADR 0093), and the plan's own P39-S3 text names
only the four backend deliverables above, not a UI. Building one would be scope beyond what was asked -
left as a natural, low-risk future addition (the response envelopes already match the established
`status`/`resource`/`approval_request_id` shape every other admin-ui four-eyes flow in this project
already knows how to render, per `UserManagement.tsx`'s existing pattern for role creation).

## Rationale

- **Additive composite-rule table instead of migrating the existing simple table**: a schema
  replacement would need a one-time data migration of every existing installation's mappings and risks
  breaking the already-tested simple path for zero behavioral gain — the two mechanisms serve genuinely
  different use cases (broad "any of these groups" vs. narrow "all of these groups together") and
  compose cleanly as independent, unioned lookups.
- **Optional, configurable four-eyes instead of mandatory (unlike break-glass)**: mirrors the dominant
  pattern in this project (every OTHER four-eyes retrofit is opt-in per action type); break-glass is the
  one deliberate exception, justified by its own, much higher severity (ADR 0023). AD-group-mapping
  changes are ordinary RBAC administration, not an emergency mechanism.
- **Default role excludes "no groups at all"**: the narrower reading matches concept 4.4's own wording
  ("unmapped groups", implying groups that exist but don't resolve) and avoids an unintended, silent
  privilege grant to accounts the AD integration isn't even engaged for.
- **Config-import bypasses four-eyes, matching `realm_roles`' existing precedent**: consistency with
  this specific service's own established config-import convention outweighs a theoretical benefit of
  four-eyes-gating a promotion path that's typically already reviewed upstream.
- **No new UI this session**: matches the feature's own history (always backend-only) and the plan's
  literal four-item scope - building one would be an unrequested expansion, not a completion of a named
  gap.

## Consequences

- **`POST /ad-group-mappings`/`POST /ad-group-composite-rules` responses are now wrapped envelopes**,
  and `DELETE` on both returns `200` instead of `204` - a breaking change with verified zero real callers
  today (backend-only, no frontend, no other service touches this table/API).
- **`permission.role.update`-style ungated-by-default behavior applies to all four new action types**:
  `auth.ad_group_role_mapping.create`/`.delete`/`auth.ad_group_role_composite_rule.create`/`.delete`
  start ungated until an operator explicitly configures approval via `PUT
  /approval-config/<action_type>` on `permission-service` - no seeding needed, exactly mirroring ADR
  0130/0151's precedent.
- ~~**The default-role setting itself has no four-eyes protection**, unlike the per-mapping/per-rule CRUD -
  a compromised `admin.user_management` account could still silently grant a broad default role to
  every otherwise-unmapped AD group member without a second approver. Accepted as this session's
  deliberate scope boundary (see "Decision"); a future session could extend four-eyes to this setting if
  that risk is judged to outweigh the added friction.~~ — **closed in Phase 53 Session 1**
  ([ADR 0171](0171-ad-group-mapping-default-role-four-eyes-and-display-name-fix.md)): the deliberate
  scope boundary was reversed on explicit request, using the exact same `_maybe_defer_to_approval`
  pattern the other four mutations already had.
- **Config-service's `ad_group_mappings` import, like `realm_roles`, bypasses four-eyes** - an
  installation with `auth.ad_group_role_mapping.create` gated will see that gate silently bypassed by a
  config-service-driven import, the same characteristic `realm_roles` already has today (not a new
  inconsistency this session introduces).
- **No admin-ui CRUD surface for mapping/composite-rule/default-role management** - remains an
  API/curl-only feature for now, a natural candidate for a future session, not committed to here.
- ~~**A mapping/rule created via the four-eyes/consumer path records the approver's raw Keycloak `sub`
  as `created_by`/actor**, not their `preferred_username` like the direct (ungated) path does - the
  approval-request payload carries no display-name field. A cosmetic inconsistency (verified live: the
  underlying identity is still correct and traceable), not a functional defect; extending the approval
  payload to also carry a display name would be a larger change with its own trade-offs, not attempted
  here.~~ — **closed in Phase 53 Session 1** ([ADR 0171](0171-ad-group-mapping-default-role-four-eyes-and-display-name-fix.md)):
  a precise re-read of the code found this bullet's own wording imprecise (it's the raw
  `initiated_by`/filer, not the approver) and resolved it server-side at execution time via the
  already-established `admin_users.find_user_by_id` reverse-resolution primitive (ADR 0069) - a smaller
  fix than the "extend the approval payload" option this bullet itself floated, and the approval
  payload/schema stayed untouched.
