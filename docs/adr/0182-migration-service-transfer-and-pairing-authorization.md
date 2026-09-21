# 0182 — migration-service: gate transfer/pairing endpoints, validate `base_url`, derive `created_by`

**Status:** accepted
**Context:** P59-S5 (Phase 59, "Critical Authorization Bugs" — fifth and last session of the sixth
gap-analysis round's live-code security sweep, closing the phase's five criticals). `migration-service`
had the most complex exploit chain of the five: `POST /transfers` and `POST /paired-installations` were
gated only by `license_gate` (checks the INSTALLATION's license status, never the caller's identity) —
neither read `X-DMS-Principal` nor called `permission-service`. `TransferCreate.created_by` was a plain
client-supplied field, never cross-checked against the real caller. Meanwhile every actual folder/document
read during a transfer runs through `LocalDmsClient`, which always authenticates as the fixed, elevated
`"migration-service"` system principal — bypassing the initiating user's own per-folder ACLs entirely.
Combined, any authenticated user with a non-demo license could (1) `POST /paired-installations` with an
attacker-controlled `base_url` (`PeerClient` does `httpx.Client(base_url=base_url, ...)` with zero
validation — a direct SSRF vector); (2) `POST /transfers` naming ANY `source_folder_id` — including ones
they cannot read — and the attacker's own paired installation as the target, so the BPMN transfer flow
walks the entire source subtree under the service's elevated identity and pushes it to the attacker's
`base_url`, optionally trashing the source afterward; and (3) forge the audit-trail actor via the
client-controlled `created_by` field throughout.

## Decision

Three independent sub-fixes, all touching the same two endpoints:

- **Caller-permission checks.** `POST /transfers` now requires the caller's own `folder.read` on
  `source_folder_id`, checked directly against `permission-service` (`_require_source_folder_read_permission`,
  same generic `check()` shape as `document-service`/`signature-service`'s equivalents — this service has
  no folder-specific convenience wrapper of its own). `POST`/`DELETE /paired-installations` now require a
  brand-new domain-admin capability, `admin.migration_management` (role `domain-admin-migration`) —
  pairing with another installation is an admin-level trust decision (Konzept 7.2), not something every
  licensed user should be able to do, and no existing capability fit this concern (same "new domain per
  genuinely new admin concern" precedent as `admin.legal_hold`/`admin.records_quarantine`).
- **`base_url` SSRF validation** (`_validate_peer_base_url`), applied at paired-installation creation
  time (no rotation endpoint exists yet to also gate). Resolves the hostname and rejects
  loopback/private/link-local/reserved/multicast/unspecified targets — `ipaddress`'s standard classification
  covers `169.254.169.254` (cloud metadata) via the link-local check with no special case needed. A
  coarse, creation-time check, not a per-request guard — does not protect against DNS rebinding between
  creation and actual transfer use, an accepted residual gap for a first pass.
- **`created_by` derived server-side from `X-DMS-Username`**, never trusted from the request body anymore.
  `TransferCreate.created_by` removed from the schema entirely (not just validated-and-ignored) — the
  field is genuinely meaningless now, so keeping it would only invite confusion about which value actually
  wins. `migration-console`'s create-transfer form's "Started by" input removed accordingly (it only ever
  fed this now-gone field); the value is still returned on every `Transfer` response, just always the
  caller's own verified identity.

**Loopback exemption for this project's own test suite.** `migration-service`'s test suite deliberately
pairs an installation with itself via `http://localhost:8000` (no real second installation is feasible in
this sandbox, see `docs/services/migration-service.md` "Deliberate limitations") — the SSRF guard would
otherwise always reject it. New setting `allow_loopback_peers` (default `False`) exempts ONLY loopback,
never private/link-local/etc., set `true` explicitly in `infra/docker-compose.yml` for this dev/test stack
and documented there as never something a real installation should need.

## Rationale

- **Why `folder.read`, not a transfer-specific capability**: reading a folder to migrate it is,
  permission-wise, the same concern as reading it for any other reason — reusing the exact capability
  `folder-service` itself checks for `GET /folders/{id}` keeps one consistent meaning for "may read this
  folder" rather than a parallel grant that would need to be kept in sync by hand.
- **Why `admin.migration_management` is a NEW capability, not a reuse**: no existing domain-admin role
  covers "may define a trust relationship with another installation" — the closest candidates
  (`admin.object_config`, `admin.user_management`, both already self-granted to `migration-service` for
  its OWN bootstrap needs) govern unrelated concerns. Reusing either would have coupled "may configure
  object types" or "may manage users" with "may pair with an external installation," a materially
  different risk profile.
- **Why `created_by` is removed from the schema rather than validated-and-ignored (unlike P59-S3's
  `signer_principal_id`, which stays and is checked for equality)**: `signer_principal_id` names WHO is
  signing, a meaningful choice distinct from the caller in principle (even though this project currently
  requires them to match) — `created_by` on a transfer never had that distinction; it was always meant to
  record who initiated the action, which the platform can now establish unambiguously itself. Keeping a
  field whose value the server always overrides would be actively misleading to any future reader of this
  schema or the frontend form.
- **Why `allow_loopback_peers` exempts only loopback, not all private ranges**: the test suite's own need
  is exactly `localhost` (127.0.0.1) — broadening the exemption to all private/link-local ranges "for
  convenience" would meaningfully weaken the guard for zero additional test-suite benefit.
- **Why rotation-time validation isn't built**: no `PUT`/rotate endpoint for `paired_installation.base_url`
  exists in this codebase today — nothing to gate yet. Noted so a future session adding rotation doesn't
  silently reintroduce this gap.

## Consequences

- New endpoints/behavior: `POST /transfers` now `401`/`403`s on the caller's own `folder.read`;
  `POST`/`DELETE /paired-installations` now `401`/`403` on `admin.migration_management`; `POST
  /paired-installations` now `422`s for a `base_url` resolving to a non-loopback private/internal address.
- `libs/dms-permission-client` (`dms_permission_client.PermissionServiceClient`) added as a new dependency
  of `migration-service` (`pyproject.toml`).
- `services/permission-service/src/permission_service/repository.py`: new `DOMAIN_ADMIN_ROLES` entry,
  `("domain-admin-migration", "Migrations-Installationspaarung verwalten", ["admin.migration_management"])`.
- `apps/migration-console`: `TransferConsole.tsx`'s "Started by" form field removed (state, effect, input,
  and the now-unused `user` destructure); `api.ts`'s `createTransfer` no longer sends `created_by`; both
  i18n dictionaries' now-dead `createdByLabel` key removed.
- New/updated tests: `migration-service` +7
  (`test_create_paired_installation_without_principal_header_is_401`,
  `test_create_paired_installation_without_permission_is_403`,
  `test_delete_paired_installation_without_permission_is_403`,
  `test_create_paired_installation_rejects_ssrf_target`,
  `test_create_paired_installation_rejects_loopback_without_flag`,
  `test_create_transfer_without_principal_header_is_401`,
  `test_create_transfer_without_folder_read_permission_is_403`), 18/18 total. Every existing call site
  updated with the now-required `X-DMS-Principal`/`X-DMS-Username` headers (a shared `_client()` default
  covers most; per-call overrides where a distinct/deliberately-wrong identity is being tested). **Scope
  note**: the new `folder.read`-missing test uses an unregistered `source_folder_id` (fails closed by this
  project's existing "unregistered resource denies" default) rather than a REAL folder a caller genuinely
  lacks access to — `folder.read` is a baseline "everyone" grant for ordinary, non-teamspace folders (ADR
  0149), so a true negative case needs a teamspace-scoped folder fixture, out of this session's scope (same
  limitation P59-S3 already accepted for `document.read`/`.write`).
- `migration-service`/`permission-service` rebuilt and redeployed (permission-service first, for the new
  role's self-healing seed); `migration-console` rebuilt and redeployed (`next build` type-checked
  successfully, confirming the schema-field removal is consistent frontend-to-backend). Live-verified
  against the real running stack: `curl` confirmed `401`/`403` for both new gates and `422` for a private
  and a link-local `base_url`; a full real transfer (self-loopback pairing, dry run) succeeded end-to-end
  with `created_by` correctly showing the caller's own `X-DMS-Username`, not a client-supplied value.
  Throwaway installation/folder cleaned up afterward. No interactive browser session was available in this
  environment to click through `migration-console`'s UI directly — verification for the frontend change is
  limited to the successful type-checked build and the page serving `200` after redeploy, not an
  interactive click-through.

**Phase 59 ("Critical Authorization Bugs") is now fully closed** — all five findings from this round's
live-code security sweep (notification-service, storage-service, signature-service, registry-service,
migration-service) are fixed, tested, and live-verified.
