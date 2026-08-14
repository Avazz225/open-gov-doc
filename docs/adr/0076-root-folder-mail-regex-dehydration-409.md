# 0076 — Root Folder Protection, Format-Derived Mail Detection, Dehydration 409

**Status:** accepted (Session 11 of 11, final session in Phase 19, see `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 19 Session 11, affects `folder-service`, `mail-connector`,
`document-service`, `apps/user-ui`

## Decision

Three independent, small gaps from the "Open Points" triage, bundled into one session:

1. **`folder-service`: `root` as a protected special folder.** `PROTECTED_FOLDER_IDS`
   (`settings.py`) previously only contained `inbox`/`outbox`, not `root` — `root` could be renamed,
   moved, hard-deleted, or moved to trash, even though it is the only folder with `parent_id=null`
   and is assumed by every other service to be a fixed, always-existing root. `root` is now added to
   the same `frozenset` as `inbox`/`outbox` and thereby automatically runs through the same three
   existing `409` checks in `main.py` (`update_folder`, `hard_delete_folder`, `trash_folder`) — no new
   check logic, just extended membership in the already existing set.
2. **`mail-connector`: candidate detection from the actually configured formats instead of hardcoded.**
   `matching.py`'s candidate regex was a generic `[A-Za-z0-9]{2,10}[-/][A-Za-z0-9]{2,10}` — it happened
   to cover the two default formats, but not installation-specific `kennzeichen_format`/
   `CaseNumberConfig.format` values that deviate from them. New `build_candidate_pattern(formats)`
   derives the pattern directly from the `{Placeholder}` format strings of `object-type-service` and
   `case-service` (the first format→regex reverse conversion in the project; previously `str.format()`
   was only ever used forward). The result is freshly loaded per incoming message (not cached once),
   with a fallback to the old generic pattern if either of the two cross-service calls fails.
3. **`document-service`: `409` instead of `404` when downloading disposed (dehydrated) document
   content.** `GET /documents/{id}/content` and `GET /documents/{id}/versions/{n}/content` now check
   `document.dehydrated_at is not None` BEFORE the storage call and return `409` with a note about the
   necessary retrieval — previously the failed storage download returned a generic `404`, which looked
   identical to a real data inconsistency (see
   `test_download_content_returns_404_instead_of_crashing_if_object_missing`). `apps/user-ui`'s
   `PreviewPane.tsx` now visibly displays this `409` message — the previous download error handling
   was completely silent (nothing appeared for `409` or for any other error).

## Rationale

- **Why `root` is only protected now and not from the start**: the original special-folder logic
  (P15-S1/S3) was built for `inbox`/`outbox` as new, additional special folders — `root` already
  existed before that and was identified during research for this session as an overlooked gap (no
  user had ever tried to rename `root`; the behavior was a bug of omission, not deliberately
  left-open behavior).
- **Why no new check logic was needed**: all three protection points (`main.py:539-545`, `:605-608`,
  `:635-639` per research) already check against `folder_id in PROTECTED_FOLDER_IDS` as a set, not
  against a fixed list of individual IDs — a pure configuration change suffices.
- **Why the candidate regex is derived from real formats instead of a second hardcode**: an
  installation can configure `kennzeichen_format`/`CaseNumberConfig.format` arbitrarily (2.2/2.5) — a
  hardcoded pattern that only happens to match the defaults would silently stop finding candidates on
  any deviation (e.g. a three- instead of four-digit year, an additional separator), without this
  becoming visible anywhere.
- **Why reloaded per message instead of cached once**: a one-time fetch at app startup would only
  recognize newly created object types/formats after a restart — the cost (two additional
  cross-service calls per incoming mail) is negligible given the message volume. An important
  technical reason discovered during implementation: a reused, long-lived HTTP client from
  `app.state` (bound to the event loop of the lifespan context) causes
  `RuntimeError: ... bound to a different event loop` in tests that call `_ingest_message()` directly
  rather than via `TestClient`'s request dispatch — fresh, short-lived client instances per call
  structurally avoid this problem.
- **Why `409` instead of `404` for dehydration**: `404` ("not found") and "deliberately removed from
  primary storage but recoverable" are semantically different — a user seeing `404` has no indication
  that a retrieval is even possible. `409` (conflict: the document's current state does not allow the
  requested operation) with an explanatory `detail` follows the same pattern as this service's already
  existing `409` responses (e.g. special-folder protection in `folder-service`, see above).
- **Why no `archival_transfer_id` link in the error message**: `document.dehydrated_at` carries no
  reference to the associated `ArchivalTransfer`/`CaseArchivalTransfer` — a link would have required a
  new data field, which would have gone beyond the scope of this small, isolated session. The message
  deliberately remains generic ("must first be retrieved").

## Consequences

- **Tests**: `folder-service` 120 (previously 116, +4: rename/move/hard-delete/trash for `root`,
  mirroring the existing inbox tests). `mail-connector` 33 (previously 30, +3: targeted unit tests for
  `build_candidate_pattern`, see the regression finding below; one test date additionally had to be
  changed from a hex to a purely numeric suffix, since the new, stricter regex recognizes
  `Laufende_Nummer` as `\d+` instead of generic alphanumeric — the real, system-generated file
  reference number suffix is always numeric anyway, see `_render_kennzeichen`).
  `document-service` 234 (previously 233, +1: new `409` round-trip test incl. rehydration).
  `apps/user-ui`: 171 tests (previously 169, +2: `409`-specific and generic download error message),
  `tsc`/`eslint`/`next build` clean.
- **Two real bugs found and fixed during live verification** (both only surfaced because this session
  was the first to run a real SMTP→POP3 round trip with an installation-specific format deviating
  from the default, instead of only testing the already-matching default formats):
  1. **`infra/docker-compose.yml`'s `mail-connector` block had no `DMS_OBJECT_TYPE_SERVICE_BASE_URL`**
     — fell back in the container to `Settings`'s local-dev default (`http://localhost:8007`), which
     points nowhere there. `list_kennzeichen_formats()` therefore failed on every incoming message
     (with a log warning), and the candidate pattern silently used the generic fallback — functionally
     unnoticeable for the two default formats (which the fallback happens to cover), but exactly this
     session's new capability (recognizing installation-specific formats) was thereby completely
     ineffective. Fixed by adding the variable (same pattern as every other service in this project)
     plus `depends_on: object-type-service`.
  2. **`build_candidate_pattern` sorted the formats alphabetically instead of by length** — Python's
     `re` alternation is "first matching alternative wins," not longest-match matching like POSIX.
     With three actually configured, distinct live formats (`{Federführung}-{Laufende_Nummer}`,
     `{Federführung}-{YYYY}-{Laufende_Nummer}`, `{YYYY}-{Laufende_Nummer}`), `sorted(set(formats))`
     alphabetically sorted the SHORTER, year-less `{Federführung}-{Laufende_Nummer}` pattern before the
     longer one — a real candidate like `P19S11Y-2026-004` was thereby incorrectly truncated already
     after `P19S11Y-2026` (the first alternative `\S+?-\d+` already found its complete, shorter match
     there and was never replaced by the second, more correct alternative). Fixed by sorting by
     descending format length (`key=lambda f: (-len(f), f)`) instead of alphabetically — the longest,
     most specific alternative is now always tried first. Three new targeted unit tests in
     `test_matching.py` (incl. a direct regression test with exactly this format combination).
- **A pre-existing, independent test infrastructure flakiness** in `mail-connector`'s
  `test_confirm_match_creates_document_in_matched_folder` (sporadic "different event loop" with a
  long-lived `app.state.virus_scan` client combined with a direct `_ingest_message()` call) was
  identified during debugging, but NOT fixed — out of session scope, confirmed via multiple isolation
  runs to have pre-existed before this session.
- **`rendering-service`/`ocr-service`/`signature-service`'s document clients** still call
  `response.raise_for_status()` without special `409` handling on
  `GET .../versions/{n}/content` — a `409` propagates there as a generic `httpx.HTTPStatusError`,
  caught by the already-existing broad `except Exception` of the respective poll loops (same error
  behavior as the previous `404`, no regression risk). No change to these three clients in this
  session — out of the scope named in the roadmap.
- **Verified live against the real running stack** (after rebuilding images for `folder-service`,
  `mail-connector`, `document-service` — `mail-connector` had to be rebuilt twice, see the two bugfixes
  described above): `root` rename/move/hard-delete/trash each return `409`; a real SMTP→POP3 round trip
  against `mailpit` with an installation-specific, attribute-based file reference number
  (`P19S11Z-2026-005`, object type "Akte", format `{Federführung}-{YYYY}-{Laufende_Nummer}`) generated
  live via `document-service` is, after both bugfixes, correctly and fully recognized as
  `status="proposed_match"` with a matching `document_id` (two intermediate attempts with colliding or
  truncated candidates documented the two bugs above); a dehydrated document returns `409` with a
  retrieval note instead of `404`, and `200` again after rehydration.
