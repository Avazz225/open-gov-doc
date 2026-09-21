# 0183 — federation-hub-service: `callback_base_url` SSRF guard; ocr-service: per-document IDOR fix

**Status:** accepted
**Context:** P60-S1 (Phase 60, "High-Severity Findings" — first session of the sixth gap-analysis round's
live-code security sweep, following on from Phase 59's five criticals). Two findings, same
"unauthenticated-adjacent SSRF/IDOR" shape, bundled since both are small-to-medium fixes following an
already-proven pattern elsewhere in this project.

**(a) `federation-hub-service`.** `POST /installations`'s `callback_base_url` had no scheme/target
validation at all. Combined with self-registration requiring no admin approval (ADR 0039's own deliberate
open-registration model, unrelated to this finding and not revisited here), an attacker could register an
installation whose `callback_base_url` points at an internal-only target (e.g. a cloud metadata endpoint),
then force the hub to make a server-side HTTP call there via `POST /handovers` delivery — a real SSRF
pivot from a fully unauthenticated starting position, using the address-book-style `GET /handovers/{id}`
status field as a blind success/failure oracle.

**(b) `ocr-service`.** `GET /ocr-results`, `GET /ocr-results/{id}`, `POST /ocr-results/{id}/retry`, and
`GET /ocr-results/{id}/page-image` checked only the coarse, "everyone"-granted `ocr.read`/`ocr.write`
capability — never the source document's own per-document ACL. `rendering-service` had the *identical* bug
shape and closed it at Phase 50 Session 3 with `_require_rendition_document_permission`; `ocr-service`'s
analogous endpoints were simply never given the same fix.

## Decision

- **`_validate_callback_base_url`** in `federation-hub-service`, called on `POST /installations` before
  signature verification even runs (cheap syntactic/DNS check first). Resolves the hostname and rejects a
  loopback/private/link-local/reserved/multicast/unspecified result. **Deliberately NOT https-only and NOT
  a hard failure on an unresolvable hostname**, a corrected design after checking real usage: this
  project's federation model has no TLS anywhere (message-level signing via ADR 0039's certificates
  instead of transport security), and this service's own extensive existing test suite (~15+ call sites)
  deliberately registers installations with genuinely non-resolving RFC 2606 test domains
  (`*.test`/`*.invalid`) while mocking the actual delivery transport — hard-requiring `https` or rejecting
  unresolvable hosts (the plan's own originally-floated design) would have broken that established,
  deliberate convention for no real security gain against this specific finding's exploit shape (a
  literal or resolving-to-private target).
- **`_require_ocr_document_permission`** in `ocr-service`, mirroring `rendering_service.main.
  _require_rendition_document_permission` verbatim in shape: checks the caller's own `document.read`/
  `.write` against `resource_id=result.document_id` (ADR 0144/0154's per-document `ResourceNode`), not a
  new ocr-specific resource type. Applied to all four named endpoints, existence-first (fetch the
  `OcrResult` row, `404` if unknown, before the permission check runs) — same ordering convention as
  everywhere else in this project. `list_ocr_results` keeps the coarse `ocr.read` check for its
  cross-document listing mode (no `document_id` given), matching `rendering-service`'s own identical
  conditional shape for `list_renditions`.

## Rationale

- **Why federation-hub-service's guard is more permissive than migration-service's `_validate_peer_base_url`
  (P59-S5, ADR 0182) for the "unresolvable" case**: the two services' real callers and test conventions
  differ materially. Migration-service's test suite pairs with itself via a real, always-resolving
  `localhost` — rejecting unresolvable hosts there costs nothing. Federation-hub-service's test suite
  specifically exercises non-resolving hostnames as a deliberate stand-in for "a remote peer installation
  this sandbox can't actually reach" — hard-rejecting those would defeat the test convention outright, not
  just require a settings escape hatch. Both designs still close the literal, actually-exploitable shape
  each finding describes (a private/loopback/metadata IP given directly, or a hostname that resolves to
  one); neither protects against DNS rebinding between validation and actual use, an accepted residual gap
  in both cases.
- **Why `ocr-service`'s fix is copied verbatim from `rendering-service` rather than redesigned**: the two
  services have the identical shape (a persisted result row tied to a `document_id`, gated only by a
  coarse root-resource capability) — reusing the already-reviewed, already-shipped fix keeps one consistent
  pattern for "does this result-row endpoint respect the underlying document's own ACL" across services,
  rather than inventing a second variant.
- **Accepted regression, deliberately**: retrying a permanently-failed OCR result whose `document_id` has
  no `ResourceNode` left at all (never registered, or fully purged — `document.resource.deleted` removes
  it) now `403`s instead of the previous `200`/`failed_permanent`-with-reset-`attempts` behavior. Such a
  retry could never have succeeded anyway (nothing to OCR), so the only real loss is the ability to reset
  bookkeeping on an already-dead, orphaned row — accepted as the correct tradeoff for closing the real IDOR
  (any caller could previously retry, and read the full text/confidence/pages of, ANY OCR result for ANY
  document they had no access to at all).

## Consequences

- New endpoints/behavior: `federation-hub-service`'s `POST /installations` now `422`s for a
  `callback_base_url` resolving to (or literally being) a private/internal address. `ocr-service`'s four
  named endpoints now `403` when the caller lacks `document.read`/`.write` on the OCR result's own
  `document_id`.
- Fixed a pre-existing, unrelated test gap discovered while verifying this session:
  `services/ocr-service/tests/test_pipeline.py`'s `_delete_storage_object_for_version` called
  `storage-service`'s `DELETE /objects/{key}` directly with no identity header — broken since P59-S2's
  trusted-caller gate (ADR 0179) shipped, apparently unnoticed until this session's own full-suite run.
  Fixed by sending `X-DMS-Principal: ocr-service` (one of the six trusted callers), unrelated to this
  session's own two findings but fixed here since it was found here.
- New/updated tests: `ocr-service` +4 net (`test_list_ocr_results_unregistered_document_is_403`,
  `test_get_ocr_result_without_document_permission_is_403`,
  `test_download_page_image_without_document_permission_is_403`; `test_retry_for_a_permanently_missing_
  document_resets_attempts_but_stays_failed_permanent` renamed to `test_retry_for_an_unregistered_
  document_is_403` with its assertion changed to match the new, deliberate `403` behavior;
  `test_list_ocr_results_empty_for_unknown_document` changed to use a real, freshly uploaded document
  instead of a literal unregistered one, since that's what it actually meant to cover), 60/60 (+9 skipped
  unaffected) total. `federation-hub-service` unchanged at 75/75 (no new tests needed — the SSRF guard is
  exercised implicitly by every existing registration test still passing under the new, deliberately
  permissive-on-unresolvable design; live-verified separately via `curl` instead, see below).
- Both services rebuilt/redeployed. **Live-verified against the real running stack**: `curl` against
  `federation-hub-service` confirmed `422` for a private IP and the cloud-metadata address as
  `callback_base_url`, fired before any signature check (no valid key/signature needed to trigger it, since
  the guard runs first); `curl` against `ocr-service` confirmed `403` for an unregistered `document_id` on
  the filtered listing and `200` for the coarse cross-document listing (no `document_id`), plus a real,
  freshly uploaded document's own OCR-result listing succeeding for its owner. Throwaway test data cleaned
  up afterward.
