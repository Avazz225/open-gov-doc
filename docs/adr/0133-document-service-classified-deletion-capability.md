# 0133 — Classified-documents trash/purge gate migrated to `admin.deletion_classified`

**Status:** accepted (P32-S4, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 32 Session 4 (post-Phase-31 gap re-analysis), affects `document-service`, `user-ui`

## Decision

`document-service`'s classified-documents trash/purge gate — `GET /documents/deleted?scope=admin_classified`
and `POST /documents/{id}/purge`'s classified branch — is REPLACED (not supplemented) with a check against
the new-since-P31-S3 `admin.deletion_classified` capability (role `domain-admin-deletion-vs`), via a new
`_require_classified_deletion_permission` helper (`has_permission`, same idiom as `admin.legal_hold`/
`admin.records_quarantine`). The legacy `classified_trash_hard_delete_admin_role` `X-DMS-Roles` string
setting is removed entirely — no fallback. The REGULAR (non-classified) trash/purge gate
(`trash_hard_delete_admin_role`) is unchanged. `user-ui`'s `TrashPane.tsx` now shows its
"Verschlusssachen-Papierkorb" tab based on `permissions.includes("admin.deletion_classified")` instead of
`user.realm_roles`.

## Rationale

- **`admin.deletion_classified` had been seeded but dead since it was created**: ADR 0114 (P31-S3) added
  the capability/role to `permission-service`'s seed list and explicitly named wiring it up as a "future
  session" item, distinct from that session's own scope (who may *classify* a document, not who may
  *purge* a classified one). Confirmed via grep that nothing anywhere called `has_permission` for it before
  this session.
- **Why REPLACE rather than supplement, same reasoning as ADR 0073's `admin.quarantine` migration**:
  `classified_trash_hard_delete_admin_role` was, from the start, only a placeholder mechanism (a plain
  string comparison against an unverified `X-DMS-Roles` header), not a standalone, conceptually anchored
  second gate — ADR 0114 itself already classified it, alongside `trash_hard_delete_admin_role`/
  `kennzeichen_admin_role`/`quarantine_release_admin_role`, as one of the pre-`has_permission`-era settings
  "never migrated." A dedicated capability already existed for exactly this purpose; keeping the old string
  check as a parallel fallback would mean two independent ways to reach the same gate; genuinely useful
  session capacity should go toward one correct mechanism, not defending both indefinitely.
- **Why the REGULAR trash/purge gate stays on the legacy `trash_hard_delete_admin_role` string check**: out
  of scope per this session's own mandate (classified-documents deletion specifically); `admin.deletion`
  (role `domain-admin-deletion`) exists in `permission-service`'s seed list for this but is likewise unused
  today — a separate, larger migration (it is this project's most-established, oldest-standing role
  setting, referenced from multiple places) left for a future session rather than folded in here
  opportunistically.
- **`purge_document`'s classified/non-classified branches now use genuinely different gate mechanisms**
  (an async `has_permission` call vs. the existing synchronous `X-DMS-Roles` set check) rather than a
  single shared "compute `required_role`, then check `roles`" code path as before — the two mechanisms
  are no longer interchangeable, so the branching had to move earlier, right after determining
  `is_classified`.
- **Frontend follows the same `permissions.includes(...)` pattern already established for
  `admin.legal_hold`/`admin.records_quarantine`** (`auth-context.tsx`'s `permissions: string[]`, populated
  from `GET /effective-permissions/{principal}/root`) rather than `user.realm_roles` — the classified-trash
  tab's visibility is now driven by the same system-native permission-service capability the backend
  actually checks, closing a client/server gating mismatch that would otherwise have appeared the moment
  the backend check changed (a `dms-admin` realm-role holder without `domain-admin-deletion-vs` would have
  seen a tab that immediately 403s, and vice versa).

## Consequences

- **An installation still relying on `X-DMS-Roles: <classified_trash_hard_delete_admin_role value>` alone
  (with no `domain-admin-deletion-vs` role assignment in `permission-service`) loses classified-trash/purge
  access after this session's deploy** — a genuine, intentional breaking change for that one gate; the
  operator must grant the new role via `POST /role-assignments` (same as every other `has_permission`-gated
  domain-admin capability in this codebase). No automatic migration is performed — matches ADR 0073's own
  precedent (`quarantine_admin_role` was likewise "removed without replacement").
- **`DMS_CLASSIFIED_TRASH_HARD_DELETE_ADMIN_ROLE` is no longer a recognized setting** — harmlessly ignored
  by pydantic-settings if still set in an installation's environment, but has no effect.
- **Tests**: `document-service` test suite unchanged in count (2 existing tests rewritten to grant
  `domain-admin-deletion-vs` via a real `permission-service` role-assignment instead of an
  `X-DMS-Roles` header, plus 1 new test for the missing-principal `401` case that didn't previously need
  covering since the old string-role check never distinguished missing-principal from wrong-role). `user-ui`
  +1 new test file (`trash-pane.test.tsx`, 3 tests — the component had no prior test coverage at all).
  Live-verified end-to-end against the real running stack: a real classified document was created, trashed,
  confirmed 401/403/200 on the listing endpoint, then purged (`204`) only with the real granted capability,
  `403` with the old `dms-admin` role alone.
