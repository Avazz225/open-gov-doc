# 0075 — Legal Hold RBAC (document-service, folder-service)

**Status:** accepted (Session 10 of 11, see Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 19 Session 10, affects `document-service`, `folder-service`,
`permission-service`, `apps/user-ui`

## Decision

`POST /legal-holds` and `POST /legal-holds/{id}/release` had NO permission check whatsoever in EITHER
service (5.2, since P7-S1/P7-S1b) — not even an `X-DMS-Principal` header parameter. This session
closes the gap:

1. **New domain admin role `domain-admin-legal-hold`** (`admin.legal_hold`,
   `services/permission-service/src/permission_service/repository.py`'s `DOMAIN_ADMIN_ROLES`) —
   Concept 5.2 does not name a dedicated role for legal hold; a new, dedicated domain instead of
   reusing `domain-admin-deletion` (see "Rationale").
2. **`document-service`**: new `_require_legal_hold_permission(x_dms_principal)` helper (`main.py`),
   uses a new `has_permission` method on the already-existing local `PermissionServiceClient`
   (previously only `check_read`/`check_write` for share links/WebDAV edit tokens, no `has_permission`
   for domain admin capabilities). Gated: `POST /legal-holds`, `POST /legal-holds/{id}/release`.
   `GET /legal-holds` and `GET /documents/{id}/has-active-hold` remain deliberately ungated.
3. **`folder-service`**: identical pattern, but the **first consumer of `libs/dms-permission-client`
   in this service** — folder-service previously had NO permission-service client of any kind (only
   the separate `ApprovalClient` for the four-eyes mechanism). Gated: the same two endpoints, `GET
   /legal-holds` remains ungated.
4. **`apps/user-ui`**: `RetentionPanel.tsx`/`FolderRetentionModal.tsx` only enable the legal hold
   buttons (set/release) when `permissions.includes("admin.legal_hold")` — new
   `getEffectivePermissions` API function (ported from `apps/admin-ui`) and new `permissions: string[]`
   on `AuthContextValue`, populated at login/session restore analogous to admin-ui. Server-side `403`
   remains the actual enforcement; the frontend is pure UX (buttons stay visible so the hold status
   indicator works for every viewer, but are disabled).

## Rationale

- **Why a new domain admin role instead of `domain-admin-deletion`**: a legal hold PREVENTS deletion,
  a deletion administrator PERFORMS it — opposing responsibilities in substance. The same person could
  hold both roles, but merging them into a single capability would have forced an installation to
  grant either both or neither, without the possibility of separation of duties (e.g. legal department
  sets holds, IT administration performs deletions).
- **Why NOT in the "everyone" group (unlike most targets of P19-S5/S7/S8)**: a legal hold is a
  legally significant, administrative action (5.2, "independent of the regular retention period ...
  during ongoing litigation") — the previous openness was an unchecked gap, not deliberately granted
  behavior worth preserving (unlike, e.g., `case.read`/`.write`, where case-service literally granted
  access to every authenticated user AND was, per the concept, meant to remain regular business use
  going forward).
- **Why `folder-service` gets `libs/dms-permission-client` instead of a local duplicate**: unlike
  `document-service`/`workflow-service` (both with established local clients including
  service-specific extra methods), `folder-service` had NO permission-service client yet — a new
  consumer with no existing code to preserve therefore follows the "new consumers use the shared lib"
  principle established since P19-S1.
- **Why `GET /legal-holds` AND `GET /documents/{id}/has-active-hold` remain ungated**: the hold status
  indicator (e.g. `RetentionPanel`'s "Legal hold active (set by ...)") must remain visible to EVERY
  viewer of a document/folder, not only legal hold administrators — otherwise a regular user could not
  tell why a deletion is blocked. `has-active-hold` is additionally a pure machine-to-machine callback
  from `archival-service` before every dehydration step, with no human principal.
- **Why the frontend disables rather than hides the buttons**: session concept requirement ("buttons
  active only for authorized roles") — the status display (who set a hold when and why) remains visible
  to everyone, only the ability to act is restricted.

## Consequences

- **Tests**: `document-service` 233 (previously 215, +18: new 403 test plus header additions to three
  existing legal hold tests and one `has-active-hold` test; additionally a previously undiscovered
  P19-S6 regression finding in `_grant_root_permission`, see below), `folder-service` 116 (previously
  112, +4, same pattern). `apps/user-ui`: `tsc`/`eslint`/`vitest` (169 tests, all green, four test
  files needed to add `getEffectivePermissions`/`permissions` to their mocks) and `next build` clean.
- **Sixth regression finding from P19-S6** (after the four from P19-S7, the one from P19-S8):
  `document-service`'s `test_api.py::_grant_root_permission` (used by share link/WebDAV edit token
  tests, not only legal hold) called `POST /roles` without `X-DMS-Principal` — document-service's full
  test suite had not run completely since P19-S6, but this went undetected since earlier sessions
  never checked the full set of affected services in one run. Fixed with the same
  `_grant_role_admin_permission` session fixture pattern as in prior sessions.
- **Fully verified live against the real running stack** (after rebuilding images for
  `document-service`/`folder-service`/`permission-service` + restart to seed the new role):
  `POST /legal-holds` without header → `401`, authenticated without `admin.legal_hold` → `403`, with
  the assigned role → `404` (unknown document, proving successful authorization). Identical for
  `folder-service`. `GET /documents/{id}/has-active-hold` still reachable ungated (`200`). Both
  container logs show clean starts.
- **No cascading between document-service and folder-service legal holds** (unchanged since P7-S1b,
  already documented) — both systems remain deliberately parallel, not nested.
