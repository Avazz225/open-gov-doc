# 0095 — IMAP backend: tested against mocked `imaplib`, not against a real IMAP server

**Status:** accepted (Post-roadmap Phase 24 Session 3)
**Context:** P24-S3, affects `mail-connector`

## Decision

`ImapBackend` (`backends/imap_backend.py`) is tested in `tests/test_imap_backend.py` against a fake
implementation mocked at the `imaplib` boundary, NOT against a real IMAP server running in the compose
stack — a departure from the convention otherwise consistently followed across this project, "test
against the real neighboring service, no mocking" (see `Pop3Backend`, which is already tested against the
real `mailpit` container, and `docs/services/mail-connector.md`'s tests section).

## Rationale

- **`mailpit` (the dev mail server container established in this project, at `v1.30.6`) has no IMAP
  server.** Verified via `docker run --rm axllent/mailpit:v1.30.6 --help`: the output lists
  `--pop3`/`--pop3-auth-file`/`--pop3-tls-*` (the server already used by `Pop3Backend`), but no `--imap*`
  flag whatsoever. Unlike the POP3 case (mailpit v1.15 added a POP3 server for exactly this self-loopback
  purpose), there is no structural counterpart here.
- **A new, IMAP-capable container would be a change to `infra/docker-compose.yml`** — this session's
  scope (P24-S3, one of four parallel-running Phase 24 sessions) is explicitly limited to
  `services/mail-connector/` and its own doc file, in order to avoid merge conflicts between the four
  parallel sessions. Permanently introducing a third mail test container (e.g. `greenmail`/`dovecot`)
  into the shared compose setup doesn't belong in a single, isolated, independently reviewable session of
  this scope.
- **No existing pattern for test-local ad-hoc containers**: none of the `conftest.py` files in this
  project independently spin up Docker containers from a pytest fixture (spot-checked across all
  `services/*/tests/conftest.py`) — introducing such a pattern here, just for a single test, would be its
  own, non-trivial test infrastructure decision.
- The fake implementation replicates the subset of `imaplib` actually used by `ImapBackend`
  (`login`/`status`/`select(..., readonly=True)`/`uid("search", ...)`/`uid("fetch", ...)`/`close`/
  `logout`) exactly in its real RFC 3501 response shape (in particular `uid("fetch", ...)`'s
  tuple-in-list structure), not just generic duck typing — reducing the risk of the mock faking behavior
  a real server wouldn't actually have.

## Consequences

- The tests prove that `ImapBackend` correctly handles `imaplib`'s response shapes (parsing of
  `UIDVALIDITY`/UID lists/fetch tuples, `BODY.PEEK[]` instead of `RFC822` to avoid `\Seen` side effects,
  `readonly=True` on `select`) — they do NOT prove that any specific real IMAP server (Dovecot, Exchange,
  Gmail, ...) delivers exactly the same response shapes. For `poplib`/`Pop3Backend` this residual risk
  doesn't apply, because that one is tested for real against `mailpit`.
- This session's live verification (see PROGRESS.md) closes this gap as best as possible: a temporary
  `greenmail` container, NOT entered into `infra/docker-compose.yml` (only for the duration of the manual
  verification, removed afterward), confirmed the full receive path against a real IMAP server.
- **Open point for a future session**: should `mail-connector` be operated against IMAP in production, a
  permanent IMAP test server in the compose stack (analogous to mailpit's POP3 server) would be a
  worthwhile addition — `test_imap_backend.py` could then be replaced or supplemented with real
  end-to-end tests, exactly as with `Pop3Backend`.
