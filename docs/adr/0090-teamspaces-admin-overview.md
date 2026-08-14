# 0090 — teamspace-service: installation-wide admin overview + new domain `admin.teamspace_management`

**Status:** accepted (Post-roadmap Phase 22 Session 5)
**Context:** Post-roadmap Phase 22 Session 5, affects `teamspace-service`, `permission-service`, `admin-ui`

## Decision

`GET /teamspaces` has always returned only the teamspaces the requesting principal is themselves a member
of (`repository.list_teamspaces_for_principal`, join via `teamspace_member`) — teamspaces are, per Concept
2.5, deliberately self-managed, with no administrative pre-provisioning. There was previously **no** way
to see all existing teamspaces installation-wide, independent of one's own membership.

1. **New endpoint `GET /admin/teamspaces`** (`teamspace-service`) — returns ALL teamspaces
   (`repository.list_all_teamspaces_with_member_counts`, `outerjoin` + `GROUP BY`), each row additionally
   carrying `member_count` instead of a full member list (saves a second, gated members route for a
   purely informational overview endpoint).
2. **New capability `admin.teamspace_management`**, new pre-seeded domain
   `domain-admin-teamspaces` (`permission_service.repository.DOMAIN_ADMIN_ROLES`) — same pattern as every
   previous new admin domain (e.g. `domain-admin-license`, P9-S1). `GET /admin/teamspaces` checks this
   capability via a new `PermissionServiceClient.has_permission()` (identical pattern to
   `document_service.permission_client.PermissionServiceClient.has_permission`).
3. **New admin UI page `/teamspaces/`** (`TeamspacesAdmin.tsx`) — a plain status table (name,
   description, created by, member count, created at), gated both via `RequireCapability` AND a gated
   sidebar entry (unlike P22-S3's `ApprovalSettings` — here real server-side enforcement exists, so a
   client-side gate doesn't fake anything).

## Rationale

- **Why a new endpoint instead of extending `GET /teamspaces` with an admin mode**: `GET /teamspaces` is
  deliberately membership-filtered — an optional query parameter that bypasses that filtering given
  sufficient capability would have made the same path carry two fundamentally different security models
  (implicit self-filtering vs. explicit permission check). A dedicated path (`/admin/teamspaces`,
  convention borrowed from other services, e.g. `document-service`'s `/documents/due-for-archival`) makes
  the distinction visible in the routing itself.
- **Why a new, dedicated capability instead of reusing `admin.user_management`**: Concept 4.6 explicitly
  describes domain-separated admin roles — every new administrative capability gets its own domain
  (ADR 0023), same principle as `domain-admin-license`/`domain-admin-query-console` among others. Reusing
  `admin.user_management` would have conflated "user/rights management" and "teamspace oversight"
  substantively, even though they are independent responsibilities (one person might need one without the
  other).
- **Why `member_count` instead of a full, expandable member list** (unlike P22-S2's groups UI): the
  existing `GET /teamspaces/{id}/members` endpoint requires `_require_member` — an admin without their own
  membership could not use it. A second, gated members route would be disproportionate for a purely
  informational overview page; the count suffices for the purpose "how many teamspaces exist, how actively
  are they used".
- **Verification detail noticed during the live test (not a code bug, but a reminder for future live
  verifications)**: `X-DMS-Principal`, which the gateway sets from the access token, is the Keycloak `sub`
  claim (for technical kick accounts like `users-admin` a short numeric ID, e.g. `"2"`), NOT the username.
  A role assignment made directly against `permission-service` by username therefore does not
  automatically apply to calls made through the gateway — `GET /auth-service/me` returns the actual `sub`
  value. Earlier live verifications in this project that assigned roles directly by username mostly did
  so for calls that set the `X-DMS-Principal` header manually via `curl` (service-to-service test
  pattern) rather than going through the gateway.

## Consequences

- **Migration**: none (no new table, no changed column).
- **`Teamspace`'s self-management remains unchanged** — creating/joining/managing stays possible for
  every authenticated principal without a capability gate, only the new installation-wide overview is
  gated.
- **Tests**: `teamspace-service` 45 (previously 41, +4: two `403` cases without principal/without
  capability, an end-to-end test across two different teamspaces with different creators confirming that
  a non-member with the capability sees both including correct member counts, plus a repository unit
  test); `permission-service` unchanged at 137 (only one new, pre-seeded role, already covered by the
  existing generic `ensure_domain_admin_roles` mechanism, no new test case needed for it). `admin-ui` 191
  (previously 185, +6: four new tests in `teamspaces-admin.test.tsx`, two new visibility tests in
  `admin-sidebar.test.tsx`).
- **Verified live against the actual running stack** (image rebuild + restart of
  `teamspace-service`/`admin-ui`, `permission-service` additionally rebuilt so the new pre-seeded role
  actually exists in the running container): `GET /admin/teamspaces` without capability → `403`; after a
  real role assignment to the correct `sub` principal → `200`; two real teamspaces with different
  creators were created via the gateway, and the admin principal (a member of neither) saw BOTH in the
  overview including the correct member count — confirmed by a parallel `403` on `GET /teamspaces/{id}`
  for the same principal (the regular, membership-filtered route stays unchanged). Test data was
  subsequently deleted. No interactive browser test (no browser/Playwright available in this development
  environment, project-wide established practice).
- Docs: new [ADR 0090](0090-teamspaces-admin-overview.md), `docs/services/teamspace-service.md`
  (API table, new section "Installation-Wide Admin Overview", tests section),
  `docs/services/permission-service.md` ("Domain-Separated Admin Roles" section), `docs/services/
  admin-ui.md` (page table, new section "Teamspaces Admin Overview", backend integration table,
  tests section) added.
