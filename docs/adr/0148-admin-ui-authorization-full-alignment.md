# 0148 — admin-ui Authorization Full Alignment + Retention/Config Domain Capabilities

**Status:** accepted (Session 3 of Phase 38, see `IMPLEMENTATION_PLAN.md`)
**Context:** Post-Roadmap Phase 38 Session 3, affects `document-service`, `folder-service`,
`object-type-service`, `storage-service`, `signature-service`, `notification-service`,
`permission-service`, `apps/admin-ui`, `apps/user-ui`

## Decision

The plan's own text for this session understated its scope on both halves:

1. **user-ui's `RetentionPanel`/`FolderRetentionModal`**: the plan assumed the legal-hold set/release
   buttons were still ungated. They were not — ADR 0075 (P19-S10) already closed that gap. The SAME
   components' "Save" button (`retention_until`/`full_deletion`/`reason`, a materially different
   action) called `PUT /documents/{id}/retention` (and the folder equivalent), which had **zero**
   permission check, client or server side. This session redirects to that real gap instead.
2. **admin-ui's "inconsistent authorization surface"**: an audit found this was not merely a missing
   frontend affordance on a few pages — of ~27 admin pages, only 3 had any client-side capability
   check, and critically, **most of the corresponding backend endpoints had no RBAC at all either**,
   not just a missing frontend wrapper. One already-seeded capability (`admin.storage`) had never been
   enforced anywhere in the codebase. Given the choice (see the user's own decision when asked) to do
   full alignment rather than a narrow subset, this session designs and wires up every missing
   capability domain, not just the frontend affordances for already-enforced ones.

### New/reused capability domains

| Capability | Role | Scope | New or reused |
|---|---|---|---|
| `admin.retention` | `domain-admin-retention` | `PUT /documents/{id}/retention`, `PUT /folders/{id}/retention`, `PUT /retention-config`, `PUT /trash-config` (both document-service and folder-service) | New |
| `admin.document_config` | `domain-admin-document-config` | document-service's `PUT /upload-config`, `/export-config`, `/audit-trace-config`, `/audit-trace-role-overrides/{role}` (PUT+DELETE), `/share-link-config` | New |
| `admin.signature_config` | `domain-admin-signature` | signature-service's `PUT /signature-config` | New |
| `admin.notification_config` | `domain-admin-notification` | notification-service's `EmailTemplate` CRUD (`PUT /email-templates/{use_case}`, `.../by-domain/{domain}`, `DELETE /email-templates/{id}`) | New |
| `admin.object_config` | `domain-admin-config` (existing) | object-type-service's own `POST`/`PUT`/`DELETE /object-types`, `PUT`/`DELETE /object-types/{id}/layouts/{purpose}`, `PUT /kennzeichen-config` | Reused — already governed workflow-service's BPMN/DMN definitions since P6-S6, but was never wired up in object-type-service itself despite its own seeded description already naming "object type" configuration |
| `admin.storage` | `domain-admin-storage` (existing) | storage-service's `PUT /guard-config`, `POST /guard-status/{id}/reidentify`, `PUT /guard-status/{id}/config`, `PUT /operational-config` | Reused — seeded since P9-S1, never enforced anywhere until this session |
| `admin.user_management` (existing) | `domain-admin-users` | permission-service's `GET /delegations` (installation-wide overview branch only) and `DELETE /delegations/{id}`'s admin-role branch (migrated off the legacy `X-DMS-Roles`/`delegation_revoke_admin_role` string check) | Reused |

`GET /delegations` keeps self-service ungated: a caller filtering by their OWN
`delegator_principal_id`/`deputy_principal_id` needs no permission (mirrors `user-ui`'s
`DelegationsPane.tsx`, which always filters this way); only querying with **no** filter (the
installation-wide overview, `admin-ui`'s `DelegationsAdmin.tsx`) or for a **different** principal (a
snooping vector this also closes) requires `admin.user_management`.

### admin-ui frontend wrappers added

`RequireCapability` wrappers added to: `object-types`, `kennzeichen-settings`, `config-packages`
(capability already existed, wrapper was missing), `upload-settings`, `export-settings`,
`signature-config`, `storage-guard`, `storage-operational-config`, `retention-settings`,
`audit-trace-settings`, `share-link-settings`, `delegations`, `approval-settings`, `email-templates`,
`query-console` (capability already existed, wrapper was missing). `AdminSidebar.tsx`'s
`requiresCapability` map extended to match.

### Deliberately left out of this session

`reports`/`forensic-trace` (reporting-service already gates via "everyone"-group capabilities per
ADR 0072, by design, not domain-admin-scoped) and `ocr-settings` (same, ADR 0073) are consistent with
their backend as-is — no change. `installations`/`registry`/`processing-failures`/`deletion-register`/
`archival-transfers`/`superuser` were judged low-priority/already-adequate (read-only, already
gated server-side, or `localStorage`-only) and left alone.

## Rationale

- **One capability per owning service/concern, not one per settings page**: `admin.document_config`
  covers five different document-service settings pages rather than five separate capabilities — all
  are the same "who administers this service's installation-wide configuration" concern. Mirrors the
  precedent `admin.object_config` already set (one capability for both process/DMN definitions AND, as
  of this session, object-type/layout/kennzeichen config).
- **`admin.retention` is a separate domain from `admin.legal_hold`**: a hold PREVENTS deletion,
  retention administration SCHEDULES it — conceptually opposite actions, the exact reasoning ADR 0075
  itself used to justify not folding legal hold into `admin.deletion`. The same retention capability is
  shared across document-service AND folder-service, since it is the same policy concern for both
  resource types (unlike `admin.document_config`, which is document-service-specific by construction).
- **`admin.signature_config`/`admin.notification_config` are their own domains, not folded into
  `admin.object_config`/`admin.storage`**: signature provider configuration (3.10) and notification
  template wording are materially different, more specialized concerns than object-type schema or
  storage-backend administration — reusing an unrelated capability would force an installation to grant
  unrelated abilities together.
- **`admin.notification_config` is deliberately separate from `notification.write` (P38-S2)**: one
  governs who may TRIGGER a single notification (a regular, if restricted, business action), the other
  governs who may change the wording every FUTURE notification of a given use case is sent with (an
  installation-wide configuration change) — same "who may use vs. who may configure" distinction this
  project already draws elsewhere.
- **`admin.object_config`/`admin.storage` reused rather than duplicated**: both already existed,
  seeded, with role descriptions that already named the exact domain now being enforced — introducing a
  second, near-identical capability would fragment a single administrative role into two grants an
  operator has to remember to keep in sync.
- **`GET /delegations` self-service exemption**: gating it unconditionally would have broken the
  existing self-service "my delegations"/"who am I a deputy for" flows in `user-ui`, which have no
  administrative character at all. The narrower rule (own-principal filter is free, no filter or
  someone else's principal needs `admin.user_management`) closes the actual gap (installation-wide
  visibility, and impersonating another principal's filter) without regressing self-service.
- **Legacy `delegation_revoke_admin_role` (`X-DMS-Roles` string check) replaced, not supplemented**:
  same "it was only ever a placeholder mechanism, not a second conceptually anchored gate" precedent as
  P32-S4's `admin.deletion_classified` migration off `classified_trash_hard_delete_admin_role`.
- **`POST .../validate`/`POST .../next-kennzeichen` on object-type-service remain deliberately
  ungated**: both are called during regular document/folder creation by any authenticated user, not an
  administrative action — gating them would have broken every document/folder upload.
- **`permission-service`'s own two new/changed endpoints check `repository.require_capability`
  directly, not `app.state.permission_client`**: permission-service has no HTTP client to itself (that
  attribute does not exist on its `app.state`) — this mirrors the existing `_require_role_management`
  helper's own internal self-check pattern, not the HTTP-round-trip pattern every OTHER service in this
  project uses to reach permission-service.

## Consequences

- **New backend RBAC gates**: document-service (+2 helpers, 9 endpoints), folder-service (+1 helper, 3
  endpoints), object-type-service (first-ever `permission_client`, +1 helper, 6 endpoints),
  storage-service (first-ever `permission_client`, +1 helper, 4 endpoints), signature-service
  (first-ever `permission_client`, +1 helper, 1 endpoint), notification-service (+1 helper, 3
  endpoints), permission-service (2 endpoints migrated/extended).
- **`infra/docker-compose.yml`**: `object-type-service`/`storage-service`/`signature-service` gained
  `DMS_PERMISSION_SERVICE_BASE_URL` + a `permission-service` `depends_on` entry (the same env-var/
  depends_on omission bug class found and fixed in P37-S1/P38-S2 — proactively checked this time before
  it could cause a live 401-via-localhost-fallback surprise).
- **Tests**: document-service 356 (was 352, +4), folder-service 140 (was 137), object-type-service 97
  (was 51, new `test_api.py` header/negative-permission coverage for its first-ever RBAC), storage-
  service 136 (was 121), signature-service 18 (was 11), permission-service 170 (was 166),
  notification-service 89 (was 84, +5 — its `EmailTemplate` endpoints had no HTTP-level test coverage
  at all before this session, only `test_repository.py`'s direct repository calls). mail-connector,
  workflow-service, query-service, and config-service each unchanged in count but had a cross-service
  test-setup fixture/production client fixed (see "Consequences" below). All backend suites `ruff
  check`/`ruff format` clean.
- **Frontend**: `apps/admin-ui` — `tsc`/`eslint`/`next build` clean, vitest 241 passed (2 pre-existing
  `admin-sidebar.test.tsx` assertions updated: the default test-permission set now includes
  `admin.object_config`, and one test's "stays visible regardless" contrast item changed from
  "Objekttypen" — now itself gated — to "Registry", which carries no capability at all). `apps/user-ui`
  — `tsc`/`eslint`/`next build` clean, vitest 270 passed (two existing test files' `permissions` mocks
  extended with `admin.retention`).
- **Cross-service test fixture ripple**: object-type-service's own tests were the direct target, but
  THREE other services' test suites create/delete real object types against the live object-type-
  service as test setup (`folder-service`'s `test_object_type_validation.py`, `document-service`'s
  `test_metadata_integration.py`/`_create_object_type` helper/four inline calls in `test_api.py`,
  `signature-service`'s `aes_required_object_type` fixture) — each needed its own dedicated
  `admin.object_config`-granted test principal added to that service's own `conftest.py`, since none of
  them previously needed any permission to do this.
- **A full backend-suite run (`./scripts/run-tests.sh` across all ~30 services) found three more
  production callers of `object-type-service`'s now-gated endpoints that this session's own service-by-
  service test runs had not exercised**: `config-service`'s `ObjectTypeServiceClient` (`config.import`,
  7.3) and `query-service`'s `ObjectTypeClient` (the `object_type.update` manipulation action, 6.1)
  both sent no `X-DMS-Principal` at all — fixed with the identical fixed-service-identity pattern
  (`X-DMS-Principal: config-service`/`query-service`, each granted `admin.object_config` via a one-time
  role assignment; `config-service`'s identity already held it from its own pre-existing self-bootstrap,
  `query-service`'s needed a fresh grant). `mail-connector`'s `VirusScanClient` similarly sent no
  `X-DMS-Principal` to `virus-scan-service`'s `POST /scan` (a **P38-S2** miss, not this session's own
  gate) — fixed with a fixed `X-DMS-Principal: mail-connector` identity (that capability is in
  "everyone", so no new grant was needed). None of these three were caught by any single service's own
  test suite — `query-service`'s `test_manipulation.py` exercises `object_type.update` only against a
  fake client, and `config-service`/`mail-connector`'s own suites happened not to exercise the affected
  code paths under their default test data. Only running every service's tests together, live, surfaced
  them.
- **Live-verified** after rebuilding + restarting every affected service and `apps/admin-ui`/
  `apps/user-ui`: confirmed the four new domain-admin roles (`domain-admin-retention`/
  `-document-config`/`-signature`/`-notification`) exist on a fresh seed via `GET /roles`; exercised
  `PUT /guard-config` on the real running `storage-service` end to end via `curl` (`401` without a
  header, `403` for an unrelated principal, `200` once granted `admin.storage`); and, in a real headless
  browser (Playwright) against the rebuilt `admin-ui`/`user-ui` containers: logging in as an account
  with `admin.object_config` but not `admin.storage` showed "Objekttypen" in the sidebar (page rendered
  fully) while "Storage-Guard"'s entire nav group was absent, and navigating directly to `/storage-guard/`
  by URL redirected to `/`; on `user-ui`, opening a real document's `RetentionPanel` showed the "Save"
  button disabled with the tooltip `Nur für die Rolle "Aufbewahrungsverwaltung" verfügbar` before the
  account was granted `admin.retention`, and enabled immediately after a fresh login once granted — with
  the legal-hold button independently still disabled throughout (a different, ungranted capability),
  confirming the two gates act independently as designed.
