# 0150 — `domain-admin-deletion` migrated to a real `admin.deletion` capability

**Status:** accepted (P39-S1, see Phase 39 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 39 Session 1 (RBAC completion), affects `document-service`, `folder-service`, `user-ui`

## Decision

The plan's premise for this session was stale, the same pattern as several prior Phase 38+ sessions: it
named 5 domain-admin roles (`domain-admin-storage`/`-license`/`-query-console`/`-deletion`/`-deletion-vs`)
as all lacking a technical account **and** an enforcing endpoint, citing `docs/services/auth-service.md`'s
own "Open Points" bullet almost verbatim. Verified against the current code: **4 of the 5 already have
real enforcement** — `-license` since P9-S1, `-query-console` since P8-S1, `-deletion-vs` since Phase 32
Session 4 (ADR 0133), and `-storage` since the immediately-preceding Phase 38 Session 3. None of them has
a dedicated technical account, and none needs one — all four follow the same pattern (a direct
`has_permission`/role-assignment lookup against whatever principal holds the role), matching what the
plan itself names as the correct model (`domain-admin-query-console`). Only `auth-service.md`'s own
open-points bullet was never updated as those four sessions landed; `docs/services/permission-service.md`'s
table and each service's own docs were already current.

The only genuinely open item is **`domain-admin-deletion`**: seeded with capability `admin.deletion` since
this project's earliest domain-admin-role sessions, but never actually checked anywhere. The real "regular
trash/purge admin" gate — `GET /documents/deleted?scope=admin`, `POST /documents/{id}/purge`'s
non-classified branch, and folder-service's exact structural counterparts (`GET /folders/deleted?
scope=admin`, `POST /folders/{id}/purge`) — instead used the older, pre-`has_permission`-era
`trash_hard_delete_admin_role` setting (default `"dms-admin"`), checked via a plain `X-DMS-Roles` header
string-membership test. This is precisely the gap ADR 0133 (Phase 32 Session 4) itself named as explicitly
deferred when it migrated the *classified*-documents counterpart to `admin.deletion_classified`: *"a
separate, larger migration... left for a future session rather than folded in here opportunistically."*
Presented with a build/remove choice, **the user chose to migrate it to real RBAC**, exactly mirroring
ADR 0133's own precedent.

`document-service`'s `_require_classified_deletion_permission` gained a sibling,
`_require_deletion_permission`, checking `has_permission(x_dms_principal, "admin.deletion")`; both
`list_deleted_documents`'s `scope="admin"` branch and `purge_document`'s non-classified branch now call it
instead of the string-role check. `folder-service` gained the identical `_require_deletion_permission`
helper and applies it the same way to `list_deleted_folders`'s `scope="admin"` branch and `purge_folder`.
Both services' `trash_hard_delete_admin_role` setting is **removed entirely, no fallback** — same
"REPLACE, don't supplement" reasoning ADR 0133 used for the classified case and ADR 0073 used for
`admin.quarantine`: a plain string comparison against an unverified header was never a standalone,
conceptually anchored second gate worth defending indefinitely alongside a real one. `user-ui`'s
`TrashPane.tsx` now shows its "Vollständiger Papierkorb" (full trash) tab based on
`permissions.includes("admin.deletion")` instead of `user.realm_roles`, the same `permissions`-driven
pattern the classified tab (`admin.deletion_classified`) and `RecordsQuarantinePanel.tsx`
(`admin.records_quarantine`) already use.

## Rationale

- **Why migrate rather than remove `domain-admin-deletion` as dead scaffolding**: unlike genuinely unused
  scaffolding, this capability has a real, adjacent, already-in-production analog (`admin.deletion_classified`,
  migrated one phase ago for the exact same kind of endpoint) — completing the pair is a small, low-risk,
  clearly-scoped change that removes a real inconsistency (two structurally identical gates, one on real
  RBAC, one on a hardcoded realm role) rather than leaving it. The user was offered the narrower "remove"
  option and explicitly chose to build instead.
- **Why REPLACE the legacy setting rather than keep it as a fallback**: ADR 0133's own "Consequences"
  section already anticipated and accepted the equivalent breaking change for the classified case ("an
  installation still relying on `X-DMS-Roles: <role>` alone... loses access after this session's deploy...
  the operator must grant the new role... No automatic migration is performed"). Keeping two independent
  paths to the same gate indefinitely was rejected there for the same reason it's rejected here: genuinely
  useful session capacity should go toward one correct mechanism.
- **Why folder-service is included even though the plan text only mentioned document-service by
  implication**: `trash_hard_delete_admin_role` is folder-service's own, textually identical setting,
  guarding the structurally identical "regular trash/purge admin" gate for folders — leaving one service
  migrated and the other on the old mechanism would recreate exactly the kind of asymmetry this session
  exists to close.
- **Why fix `auth-service.md`'s stale bullet instead of leaving it**: it is the literal source the plan
  text paraphrased, and per this multi-session pattern (P38-S1/S3/S4 all found similarly stale premises
  traceable to a single un-updated doc line) leaving it uncorrected would let the next planning pass repeat
  the same now-resolved research question.

## Consequences

- **Breaking change, by design, matching ADR 0133's own precedent**: an installation still relying on
  `X-DMS-Roles: dms-admin` alone (with no `domain-admin-deletion` role assignment in `permission-service`)
  loses regular trash/purge admin access on both `document-service` and `folder-service` after this
  session's deploy. No automatic migration is performed — the operator must grant the role via
  `POST /role-assignments`, same as every other `has_permission`-gated domain-admin capability.
- **`DMS_TRASH_HARD_DELETE_ADMIN_ROLE` is no longer a recognized setting on either service** — harmlessly
  ignored by pydantic-settings if still set in an installation's environment, no effect.
- **`docs/services/auth-service.md`'s "Open Points" bullet corrected**: `-license`/`-query-console`/
  `-storage`/`-deletion-vs` struck as enforced (cross-referencing each session that actually closed them);
  `-deletion` struck as resolved by this session.
- **Tests**: `document-service` and `folder-service` test suites unchanged in count (existing tests
  rewritten to grant `domain-admin-deletion` via a real `permission-service` role-assignment instead of an
  `X-DMS-Roles` header, using the identical fixture pattern already established for
  `domain-admin-deletion-vs`). `user-ui`'s `trash-pane.test.tsx` and the relevant `document-workspace.test.tsx`
  case updated the same way.
