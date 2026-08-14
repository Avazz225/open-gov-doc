# 0061 — Direct Office editing: short-lived WebDAV edit token in the Office URI start address

**Status:** accepted
**Context:** Ad-hoc post-roadmap feature (user request after completion of the 107-session roadmap), affects `document-service`, `webdav-connector`, `user-ui`

## Decision

Clicking on an Office document (`PreviewPane.tsx`) requests a new, short-lived `WebdavEditToken`
(`POST /documents/{id}/webdav-edit-tokens`, 8h TTL) from `document-service` and navigates the browser
to `ms-word:ofe|u|https://<token>:@<webdav-connector-host>/webdav/by-id/<document-id>.<ext>` (analogous
to `ms-excel:`/`ms-powerpoint:`). The locally installed Office application opens the file via WebDAV
directly for editing; saving writes back via WebDAV `PUT` into `webdav-connector`'s already existing
check-in mechanism.

Three building blocks:

1. **`WebdavEditToken` (document-service)** - new table, structurally 1:1 modeled on `ShareLink`
   (`token` as PK, `expires_at`/`revoked_at`), but additionally with `principal_id` (the identity used
   as lock holder at check-in - `ShareLink` doesn't need this since it's read-only).
   Issuance requires `document.write` (`check_write`, new in `permission_client.py`), not just
   `document.read` - an edit token grants write access.
2. **`by-id/` path resolution (webdav-connector)** - `DmsDavProvider.get_resource_inst()` gets a
   new, additive branch ahead of the existing path-based `resolve_path()`: paths with the `by-id/`
   prefix are resolved directly via `self.tree.get_document(document_id)` (method already exists
   fully in `DmsTreeClient`) instead of via the O(depth) path walk. The `.ext` extension is purely
   cosmetic/for Office's file type recognition and is discarded server-side.
3. **Token-as-username branch (`DmsAuthDomainController.basic_auth_user`)** - if the password passed
   in is empty, the username is treated as an edit token and resolved against a new, purely
   east-west-internal endpoint (`GET /internal/webdav-edit-tokens/{token}`, no gateway, no
   `X-DMS-Principal`, exactly as `DmsTreeClient` already does for all other calls). On success,
   `environ["wsgidav.auth.user_name"]` is overwritten with the resolved `principal_id`, rather than
   leaving the raw token in place - otherwise check-in would incorrectly use the token instead of the
   real identity as `created_by`. The existing username+password branch (real WebDAV mount) remains
   unchanged.

## Rationale

- **Why a token in the URL instead of a password dialog**: agreed with the user (recommendation
  accepted) - Office/Windows should start editing without a manual intermediate step. The
  `token:@` userinfo-in-URL pattern is used in practice (e.g. Nextcloud/ownCloud-style "open in
  Office" integrations), but in this sandbox without a real Windows/Office it cannot be conclusively
  verified whether the credentials dialog is reliably suppressed by it - the same sandbox boundary
  already accepted for `apps/office-addin` (ADR 0045), here deliberately documented rather than
  silently assumed.
- **Why `principal_id` is not returned to the client** (`WebdavEditTokenOut` only delivers
  `token`/`expires_at`): the client (browser) doesn't need the identity, only `webdav-connector`
  (east-west) does - avoids unnecessary data disclosure to a less trusted context.
  Explicit permission checking on every WebDAV action during the session is deliberately NOT
  repeated - checked only at issuance; a permission revocation in the interim only takes effect
  after token expiry/revocation. Same category of limitation this project has already accepted
  elsewhere (share links).
- **Why `apps/office-addin` (ADR 0045) is not a duplicate**: solves a different problem (task-pane
  add-in in an already-open, empty Word document, no WebDAV, no URI scheme, Word only). This
  feature starts editing an EXISTING DMS document directly from the browser, for all three Office
  formats. Complementary, not a replacement.
- **Why `webdav-connector` needs a new base URL never previously communicated to the browser**
  (`NEXT_PUBLIC_WEBDAV_CONNECTOR_BASE_URL`): unlike all other `api.ts` calls, which consistently go
  through the gateway, the Office URI handler must navigate directly against the WebDAV endpoint (no
  WebDAV proxying through the gateway is planned) - a build-time variable pattern analogous to
  `NEXT_PUBLIC_GATEWAY_BASE_URL`.

## Consequences

- **Deliberately deferred**: an admin UI/`MetadataPanel` surface for viewing/revoking active edit
  tokens - the read endpoint (`GET .../webdav-edit-tokens`) already exists, but the UI for it was not
  built in this session (`ShareLinkModal.tsx` is structurally almost identical and transferable).
- **Not verifiable live in this sandbox**: the actual Office start and whether the credentials dialog
  is suppressed (no Windows/Office available here) - treated as a documented limitation, not a
  blocker. The token issuance/resolution round trip itself (via `curl` against `webdav-connector`),
  however, is fully verifiable and part of the regression suite.
