# 0180 — signature-service: gate `POST /signatures` behind document permission and verified signer identity

**Status:** accepted
**Context:** P59-S3 (Phase 59, "Critical Authorization Bugs" — third session of the sixth gap-analysis
round's live-code security sweep). `signature-service`'s `POST /signatures` had **no permission check of
any kind** — any authenticated caller could sign (and thereby check in a new version of) any document in
the installation, regardless of their own `document.write` access to it. Worse, `signer_principal_id`
was taken directly from the request body with no verification against the caller's own identity — any
caller could set it to an arbitrary username, producing a signature record and certificate subject that
falsely attributes the signature to someone else. The three read endpoints (`GET /signatures`,
`GET /signatures/{id}`, `GET /signatures/{id}/verify`) had the same gap on the read side: no permission
check, and `GET /signatures` additionally allowed an unfiltered system-wide listing of every signature in
the installation with no `document_id` filter at all.

## Decision

- **New helpers `_require_document_write_permission`/`_require_document_read_permission`**, both calling
  the shared `dms_permission_client.check()` directly against `permission-service`, with the target
  document's own `id` as `resource_id` (ADR 0154's convention) and `document.write`/`document.read` as the
  permission. `create_signature` calls the write variant; the three read endpoints call the read variant.
  Unlike `document-service`'s own equivalent check, `signature-service` has no document-specific
  convenience client of its own, so this goes through the generic `check()` call rather than a duplicated
  `check_read`/`check_write` wrapper.
- **`create_signature` additionally requires `payload.signer_principal_id == x_dms_username`.** Checked
  against `X-DMS-Username` (Keycloak `preferred_username`), **not** `X-DMS-Principal` (Keycloak `sub`,
  UUID) — `signer_principal_id` is compared against `auth-service`'s own `username` field elsewhere in
  this service (`resolve_signer`), so it is itself a username, never a `sub`. Confirmed against the one
  real caller, `apps/user-ui/src/components/SignaturesPanel.tsx`, which already always sends
  `signerPrincipalId: user.username` — the caller's own username, never someone else's — so this check
  changes no real usage, only closes the spoofing path.
- **`GET /signatures` now requires a non-empty `document_id` query parameter** (`400` otherwise) instead
  of supporting an unfiltered system-wide listing. The one real caller (`user-ui`'s `SignaturesPanel`,
  via `listSignatures(token, documentId)` in `apps/user-ui/src/lib/api.ts`) always already passes it.
- **Ordering: existence-check before permission-check**, matching `document-service`'s own established,
  documented convention (`_require_document_permission`'s own docstring: "callers resolve 404 themselves
  first"). `create_signature` calls `document_client.get_document()` (elevated `X-DMS-Principal:
  signature-service` identity, 404 for an unknown `document_id`) *before* the new permission check; the
  three read endpoints call `repository.get_signature()` (404 for an unknown `signature_id`) before the
  new permission check. This was a deliberate correction during implementation — the first draft checked
  permission before existence specifically to avoid a document-existence oracle for unauthorized callers,
  but that ordering is inconsistent with the rest of this project (verified against `document-service`'s
  `GET`/`PATCH /documents/{id}`, which are existence-first end to end) and was reverted in favor of
  matching the established convention exactly, rather than introducing a second, stricter security
  posture unique to this one service.

## Rationale

- **Why `document.write`/`.read`, not a signature-specific capability**: signing a document is, from a
  permissions standpoint, indistinguishable from any other document-content-mutating action — it checks
  in a new version. Reusing the exact same capability `document-service` itself checks for `PATCH
  /documents/{id}` keeps one consistent meaning for "may write this document" across services, rather than
  introducing a parallel, signature-specific grant that would need to be kept in sync with the real
  `document.write` grant by hand.
- **Why the identity check is on `create_signature` only, not the read endpoints**: only signing writes a
  `signer_principal_id` that is later trusted as an attribution record (surfaced in the certificate
  subject and the audit trail). Reading a signature or its verification result carries no such
  attribution risk — the existing `document.read` gate on those three endpoints is sufficient.
- **Why the ordering correction matters**: an unauthorized caller learning "document X exists" via a
  404-vs-403 distinction is a real, if narrow, information leak — but this project has already accepted
  that tradeoff project-wide for the sake of one predictable, documented convention. Special-casing
  `signature-service` to be stricter would leave two different security postures for the same kind of
  check across the codebase, a worse outcome than the narrow leak itself.

## Consequences

- New endpoints/behavior: `POST /signatures` now `401`s with no `X-DMS-Principal`, `403`s without
  `document.write` on the target document or when `signer_principal_id` doesn't match `X-DMS-Username`.
  `GET /signatures` now `400`s without `document_id`, and all three read endpoints `401`/`403` the same
  way as the write path on the read side.
- `services/signature-service/tests/test_api.py`: the shared `client` fixture now sends a default
  `X-DMS-Principal: signature-service-tests` (the same literal principal `conftest.py`'s `pdf_document`/
  `non_pdf_document` fixtures already use to create the test document against the real `document-service`
  — since `document.read`/`.write` are baseline "everyone" grants for ordinary, non-teamspace documents
  (ADR 0149), no new `permission-service` role/grant was needed for this principal to pass the new gate).
  It does **not** set a default `X-DMS-Username`, since `real_signer` (also `conftest.py`) generates a
  fresh username per test — each `POST /signatures` call site now passes `X-DMS-Username` explicitly,
  matching that test's own `real_signer` value (or a deliberately mismatched one, for the two new
  negative tests). +6 new tests (`test_create_signature_without_principal_header_is_401`,
  `test_create_signature_with_mismatched_signer_is_403`, `test_list_signatures_without_document_id_is_400`,
  `test_list_signatures_without_principal_header_is_401`,
  `test_get_signature_without_principal_header_is_401`,
  `test_verify_signature_without_principal_header_is_401`); the ordering fix keeps
  `test_sign_rejects_unknown_document` passing unchanged at `404`. 32/32 total.
- **Scope note**: unlike P59-S1/P59-S2's sibling sessions, this session has no "without permission"
  (`403` for a real document the caller genuinely lacks `document.write`/`.read` on) regression test —
  `document.write`/`.read` are baseline "everyone" grants for any ordinary document (ADR 0149), so
  constructing a real negative case requires a teamspace-scoped document (`inherit=False`) with its own
  fixture machinery, which is out of this session's scope. The `401` (missing principal) and `403`
  (mismatched signer identity) tests cover the two gaps this session actually closes.
- Live-verified against the real running stack: `curl` against `signature-service` with a real document
  (created via `document-service`) confirmed `401` with no `X-DMS-Principal`, `403` with a mismatched
  `X-DMS-Username`/`signer_principal_id`, and a full real signing flow (real `auth-service` account,
  `X-DMS-Username` matching `signer_principal_id`) succeeded end-to-end through `GET .../verify` and
  `GET /signatures?document_id=...`. Throwaway test document and user cleaned up afterward.
