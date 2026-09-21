# 0189 — webdav-connector edit-token session scope + mail-connector inbound size limit

**Status:** accepted
**Context:** P61-S4 (Phase 61, "Medium-Severity Findings" — fourth and final session of the sixth
gap-analysis round's live-code security sweep). Two unrelated connector-layer findings, bundled since
each is small and neither depends on the other.

## Decision

**(a) webdav-connector: an edit-token-authenticated session is now scoped to the one document it was
minted for.** `DmsAuthDomainController.basic_auth_user()`'s Office-direct-editing branch resolves a
`WebdavEditToken` (an empty-password Basic Auth "username") to the REAL underlying user's identity via
`document-service`'s `GET /internal/webdav-edit-tokens/{token}`, then sets that identity as
`environ["wsgidav.auth.user_name"]`. From that point on, the rest of the WSGI request pipeline treats
the session exactly like a real password login for that user — nothing previously distinguished a
token-authenticated session from an ordinary one. A holder of just the edit token (e.g. leaked from an
Office URI handler, `ms-word:ofe|u|<url>`) could therefore browse/read/write/delete anything that
underlying principal has permission on via any WebDAV client, not just edit the one document
(`by-id/{document_id}`) the token was minted for.

Fixed by threading the token's target `document_id` through the request: `basic_auth_user()` now also
stores it on `environ["dms.webdav_edit_token_document_id"]` (the internal resolve endpoint's
`WebdavEditTokenResolveOut` already returns `document_id` alongside `principal_id` — no
document-service change needed). `DmsDavProvider.get_resource_inst()` checks this at the top: if set,
any path that does not resolve to exactly `by-id/<that document_id>` is rejected with `403`, before any
tree traversal or downstream client call. The bare `by-id/` namespace path itself stays reachable for
such a session (wsgidav's own PUT-parent-collection-check needs it, see `_ByIdVirtualCollection`) — it
reveals nothing, since `get_member_names()` always returns an empty list.

**(b) mail-connector: inbound messages are now rejected above a configurable size, before MIME
parsing.** `_parse_message` (`main.py`) decoded every MIME part of an inbound message fully into memory
with no cap anywhere in `mail_connector.settings.Settings` — a real DoS vector against a service with
genuine unauthenticated-adjacent external attack surface (any external sender able to reach a
configured mailbox). Fixed with a new `settings.max_message_size_bytes` (25 MiB default), checked
against the RAW message's total byte length in `_ingest_message`, BEFORE `_parse_message` is even
called. An oversized message is never parsed at all; a minimal `InboundMessage` row is still created
and immediately marked `status="rejected"` (reusing `repository.mark_rejected`, the same mechanism the
manual mail-room reject flow already uses) so the per-mailbox idempotency check
(`repository.get_by_source_uid`) does not retry it on every subsequent poll tick.

## Rationale

- **Why `get_resource_inst()` and not a narrower check inside `DmsDavDocument`/`_parse_by_id_path`
  for (a)**: `get_resource_inst()` is the single choke point every WebDAV verb (GET/PUT/PROPFIND/
  DELETE/MOVE's source path/LOCK) resolves through before anything else happens — checking there closes
  every verb at once, including ordinary folder-path browsing (`resolve_path`), which a narrower
  by-id-only check would have missed entirely.
- **Accepted, documented residual for (a)**: `DmsDavDocument.handle_move()`'s destination-path
  resolution (`self._tree.resolve_path(parent_path, x_dms_principal=actor)`) uses the real underlying
  identity's own permissions, not a second scope check — a token-scoped session can still relocate the
  one document it's authorized to touch into any folder that real user has write access to. Narrower
  than the original gap (arbitrary cross-document read/write), and Office's own check-in flow only ever
  issues `PUT`, never `MOVE`, against a `by-id/` URL — not fixed here, since closing it would need a
  second, separately-scoped check with no established precedent elsewhere in this file, for a
  meaningfully smaller residual risk than the primary gap.
- **Why a raw-byte-length check before parsing, not a per-attachment cap during parsing, for (b)**: the
  majority of an oversized message's memory cost comes from `email.message_from_bytes` building the
  MIME structure tree and the subsequent per-part base64/quoted-printable decode — both roughly linear
  in the total message size regardless of how many parts it's split into. A single upfront raw-length
  check avoids both costs entirely for an oversized message, same "reject before the expensive step"
  principle `dms_common.MaxBodySizeMiddleware` already established (ADR 0187), and is simpler than a
  per-part cap that would still pay the MIME-structure-parsing cost before rejecting anything.
- **Accepted, documented residual for (b)**: the check happens after `MailboxBackend.fetch_new_messages()`
  has already retrieved the message's full raw bytes over IMAP/POP3 — the protocol-level fetch cost
  itself isn't avoided, only the materially larger downstream MIME-parsing/decode/virus-scan/storage
  cost. Avoiding the fetch cost too would need per-message size-aware partial fetching (e.g. IMAP's
  `BODY.PEEK[]<0.N>` range syntax) — a materially larger change for a narrower residual, not attempted
  here.
- **Why 25 MiB as the default for (b)**: a common real-world mail-server attachment ceiling (matches
  Gmail's/Microsoft 365's own default), well above legitimate correspondence but far below what would
  meaningfully strain this service's memory — the same judgment-call shape as ADR 0187's 200 MiB default
  for direct file uploads (mail attachments are conventionally capped much lower than direct uploads
  across real-world mail infrastructure).
- **Why reuse `status="rejected"`/`mark_rejected` instead of a new status value for (b)**: the model's
  own docstring already describes `"rejected"` generically ("discarded by the mail room, e.g. spam"),
  not exclusively as a human mail-room action — an automatic size-based rejection fits the same
  semantics (a message that will never be processed further) without growing the status enum for a
  narrow, closely related case.

## Consequences

- `services/webdav-connector/src/webdav_connector/domain_controller.py`: `_resolve_edit_token` now
  returns `tuple[str, str] | None` (`principal_id`, `document_id`) instead of just `principal_id`;
  `basic_auth_user` sets the new `environ["dms.webdav_edit_token_document_id"]` key.
- `services/webdav-connector/src/webdav_connector/dav_provider.py`: `get_resource_inst` gained the scope
  check described above.
- `services/mail-connector/src/mail_connector/settings.py`: new `max_message_size_bytes: int =
  26_214_400`.
- `services/mail-connector/src/mail_connector/main.py`: `_ingest_message` gained the early-return size
  check described above.
- New tests: `webdav-connector` +1 (`test_edit_token_cannot_access_a_different_document` — two real
  documents, one token minted for the first, asserts the scoped document remains reachable while a
  different document by ID AND ordinary root-path browsing both `403`), `mail-connector` +1
  (`test_ingest_rejects_oversized_message_without_parsing_it` — `max_message_size_bytes` monkeypatched
  low, asserts `status="rejected"` with zero attachments, i.e. `_parse_message` was never reached).
  `mail-connector` 79/79. `webdav-connector`: 13/17 passed, 4 pre-existing failures unrelated to this
  session's change — `test_root_listing_shows_a_freshly_created_folder`/`test_put_creates_a_new_document`/
  `test_put_on_existing_path_checks_in_a_new_version`/`test_move_renames_a_document` all time out on a
  root `PROPFIND` against this shared dev stack's `root` folder, which has accumulated 121 documents
  from unrelated test runs over time — a pre-existing, already-documented condition (`_dav_client`'s own
  docstring in `test_webdav.py` names exactly this scenario as the reason for its already-generous 30s
  timeout); none of the four touch edit tokens or this session's changed code paths, and this session's
  own new test passed cleanly. `ruff` clean across both services (same pre-existing, unrelated
  repo-wide failure in `apps/libreoffice-addin` confirmed out of scope again).
- Both services rebuilt/redeployed. **Live-verified against the real running stack**: webdav-connector —
  a real user account, two real uploaded documents, one edit token minted for the first via
  `POST /documents/{id}/webdav-edit-tokens` — confirmed `200` for the scoped document
  (`GET /by-id/{scoped_id}`), `403` for the other document by ID, `403` for a root `PROPFIND`.
  mail-connector — a real small message sent via SMTP to the dev `mailpit` instance was polled and
  ingested normally (`status="unassigned"`, synthetic body-text attachment created, scan `clean`),
  confirming the new size check doesn't interfere with the golden path; the oversized-rejection path
  itself is covered by the automated regression test (a live 25+ MiB SMTP send was impractical to script
  quickly and the settings-level mechanism is identical either way).
