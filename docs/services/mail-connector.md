# mail-connector

**Responsibility:** Technical receipt/sending of external correspondence for the inbox/outbox special area (Concept 2.5), review/assignment by a dedicated mail room role, automatic assignment suggestion based on a reference number found in the subject/body (document-service) or a case number (case-service). Since Post-Roadmap Phase 31 Session 12a (14.2), any number of independently-polled named mailboxes instead of one fixed mailbox per installation.
**Concept reference:** 2.5/3.3 (connector principle)/7.1 (assignment workflow, here directly instead of via BPMN)/14.2 (multi-inbox model, Post-Roadmap Phase 31 Session 12a)
**Own Postgres schema:** `mail_connector` (`inbound_message`, `inbound_attachment`, `outbound_message`)

## API

| Method | Path | Description |
|---|---|---|
| `GET` | `/mailboxes` | Configured mailbox list (Post-Roadmap Phase 31 Session 12a, [ADR 0123](../adr/0123-multi-inbox-model-env-var-config-no-department-rbac-yet.md)) — `id`/`name`/`kind`/`owning_group_id` only, credential fields deliberately excluded. Same role gate as everything else here |
| `GET` | `/inbound?status_filter=...&mailbox_id=...` | List of received messages incl. attachments, optionally filtered by `status` and, since Post-Roadmap Phase 31 Session 12a, by `mailbox_id`. Role-gated (`poststelle_role`, default `dms-poststelle`) |
| `GET` | `/inbound/{id}` | Single message — 404 if unknown |
| `POST` | `/inbound/{id}/confirm-match` | Confirms a proposed match (`status="proposed_match"`, otherwise 409) — creates a document in the folder of the assigned document (or `folder_id`, if specified) for each clean attachment (including the body text, see below); for case-number matches, `folder_id` is mandatory (400 without it) |
| `POST` | `/inbound/{id}/assign` | Manual assignment — mandatory fields `title`/`folder_id`, optional `case_id` (adds a document reference to the circulation folder on success) |
| `POST` | `/inbound/{id}/reject` | Discards a message (e.g. spam) — `status="rejected"`, optional reason |
| `POST` | `/outbound` | Outbox — sends an external email (SMTP), logs success/failure; `sent_by` comes from `X-DMS-Principal`, not from the body. If `related_document_id` is set, the current content of the referenced document is attached as a file (400 for an unknown `related_document_id`, checked BEFORE the send attempt) |
| `GET` | `/outbound` | List of sent messages |
| `GET` | `/healthz` | Health check |

All endpoints except `/healthz` require `X-DMS-Principal` (401 without it) and the configured mail room role (403 without it).

## Multi-Inbox Model (14.2, Post-Roadmap Phase 31 Session 12a, [ADR 0123](../adr/0123-multi-inbox-model-env-var-config-no-department-rbac-yet.md))

`Settings.mailboxes: list[MailboxConfig]` replaces the previous single, service-wide `inbound_protocol`/
`pop3_*`/`imap_*` block — any number of named mailboxes, each with its own retrieval protocol/credentials,
configured via `DMS_MAILBOXES` (a JSON list, `infra/docker-compose.yml`'s comment there shows the exact
shape). First of a three-part split of the original "central + decentralized inbox model with a
cross-inbox routing registry ('Postbuch')" ask (P31-S12a config/plumbing → P31-S12b routing/hand-off →
P31-S12c searchable log), both the gap analysis and the plan itself having flagged the combined ask as too
large for one session.

- **`MailboxConfig`**: `id`, `name`, `kind` (`"central"`|`"departmental"`), `owning_group_id` (a
  `permission-service` `Group.id` by convention, no FK enforcement across the config/service boundary —
  required iff `kind="departmental"`, forbidden for `"central"`) — reuses P31-S9's `Group` rather than
  inventing a second, competing org-unit concept (user-chosen, same reasoning P31-S10 already applied for
  org-hierarchy grants' `org_unit` target). Deliberately env-var/restart-only, not a DB-backed CRUD table
  — the same choice [ADR 0091](../adr/0091-connector-operational-config-live-editable.md) already made for
  `storage-service`/`signature-service` connector credentials: making them live-editable would need new
  encryption/masking infrastructure, out of scope here too.
- **One poll loop, N mailboxes**: `_poll_loop` iterates every configured mailbox each tick (one
  `MailboxBackend` instance per mailbox, built once at startup, `app.state.backends: dict[mailbox_id,
  MailboxBackend]`) — not N concurrent poll tasks. Each mailbox's fetch/ingest runs in its own `try`/
  `except` within the tick, so one mailbox's transient failure doesn't block the others.
- **`source_uid` uniqueness is scoped per mailbox** (`UNIQUE(mailbox_id, source_uid)`, was globally unique
  before this session) — a POP3/IMAP UID is only guaranteed stable within one mail account (RFC 1939), two
  different mailboxes could plausibly reuse the same native UID.
- **`GET /mailboxes`** exposes the configured list without credentials — the basis for a future mailbox
  selector in the frontend (not built in this session, see "Open Points").
- **Ad hoc migration** (no Alembic, see CONTRIBUTING.md): `mailbox_id` added, backfilled to `"central"`
  (the fixed id of the single default mailbox this session's `Settings.mailboxes` default reproduces —
  every message that ever arrived did so through what is now named the "central" mailbox), then the old
  single-column unique constraint on `source_uid` alone is replaced with the composite one — guarded via
  `information_schema` checks so a fresh install (where `create_all` already creates the composite
  constraint directly) does no-ops on every startup.
- **Deliberately unchanged in this session**: RBAC (`poststelle_role` stays one single, global role — every
  holder sees every mailbox, central and departmental alike, exactly like today's single-mailbox model;
  per-department visibility narrowing is a real, separate RBAC design question, not solved here), outbound/
  SMTP (stays one global config; the plan's own wording is about *inbox* multiplicity), and the frontend
  (`PoststellePane.tsx`, user-ui, is untouched — no mailbox selector yet).

## Retrieval protocol (3.3, connector principle)

`backends/interface.py`'s `MailboxBackend` is pluggable like the storage backends/virus scan engines (`DMS_INBOUND_PROTOCOL`, `"pop3"` (default) and, since P24-S3, `"imap"` implemented). `Pop3Backend` (`backends/pop3_backend.py`) uses Python's standard library `poplib` — no additional package needed. `poplib` is synchronous; every call runs via `asyncio.to_thread`. Since Post-Roadmap Phase 31 Session 12a, `build_backend()` takes one `MailboxConfig` (not the whole `Settings`) — one backend instance per configured mailbox, see "Multi-Inbox Model" above.

**Development standard (POP3)**: `mailpit` (already present as the dev SMTP test server) serves as a self-loopback source — since v1.15 it ships its own POP3 server, enabled via `--pop3-auth-file`. The same mail delivered via SMTP can thus be read back over a real standard protocol without needing an external mail server (same principle as the federation hub self-loopback, P6-S9). Credentials (`infra/mailpit-pop3-auth`, dev-only) must match the default `"central"` mailbox entry's `pop3_username`/`pop3_password` in `DMS_MAILBOXES` (see "Multi-Inbox Model" above — previously the standalone `DMS_POP3_USERNAME`/`DMS_POP3_PASSWORD` env vars, now folded into the mailbox config).

**IMAP (`backends/imap_backend.py`, `ImapBackend`)**: also uses only the standard library (`imaplib`), same `asyncio.to_thread` pattern. Fetches via the UID-based command set (`UID SEARCH`/`UID FETCH` with `BODY.PEEK[]`, RFC 3501 §6.4.8) instead of the volatile sequence numbers — analogous to POP3's `UIDL`. Since a bare IMAP UID is only stable within the same `UIDVALIDITY` epoch, the `source_uid` passed on to `repository.get_by_source_uid` is composite: `f"{uidvalidity}:{uid}"`. `select` runs with `readonly=True`, and the fetch uses `BODY.PEEK[]` instead of `RFC822`/`BODY[]` — both prevent server-side `\Seen` side effects, the same restraint as POP3's deliberate omission of `client.dele()`. IMAP-specific settings live per mailbox since Post-Roadmap Phase 31 Session 12a (`imap_host`/`imap_port`/`imap_username`/`imap_password`/`imap_use_tls`/`imap_mailbox` on a `MailboxConfig` entry in `DMS_MAILBOXES` — previously standalone top-level `DMS_IMAP_*` env vars, default `imap_mailbox` still `INBOX`, IMAP folders are configurable — POP3 has no named folders).

**No development-standard self-loopback for IMAP**: `mailpit` (as of `v1.30.6`) has, unlike the POP3 case, no built-in IMAP server (`docker run axllent/mailpit:v1.30.6 --help` lists no `--imap*` flag). `ImapBackend`'s tests (`tests/test_imap_backend.py`) therefore mock at the `imaplib` boundary instead of running against a real server — see [ADR 0095](../adr/0095-imap-backend-mocked-imaplib.md) for the full rationale and the live verification performed instead against a temporary `greenmail` container.

A new protocol (e.g. Microsoft Graph for Exchange/O365) only implements `MailboxBackend`; the rest of the service remains unchanged — see "Open Points".

## Ingestion pipeline (`_ingest_message`, main.py)

1. Poll loop (`_poll_loop`, every `poll_interval_seconds`, default 20s) calls `MailboxBackend.fetch_new_messages()`.
2. Idempotency: every message carries a backend-specific, stable `source_uid` (POP3 UIDL) — already processed UIDs are skipped (`repository.get_by_source_uid`, scoped per mailbox since Post-Roadmap Phase 31 Session 12a — see "Multi-Inbox Model" above).
3. `_parse_message` breaks down the raw RFC-822 message (`email` standard library) into sender/subject/body/attachments.
4. **Matching** (`matching.py`): a regex candidate extractor finds `X-Y`-style tokens (e.g. `2026-001`) in subject+body, checks each candidate against `GET /documents/by-kennzeichen` AND `GET /cases/by-vorgangsnummer`. Exactly one match across both reference types → `status="proposed_match"`; no match or an ambiguous one → `status="unassigned"` (all candidates remain visible for manual assignment, `match_candidates`). **Since Post-Roadmap Phase 19 Session 11** ([ADR 0076](../adr/0076-root-folder-mail-regex-dehydration-409.md)), the pattern is no longer hardcoded but freshly derived per incoming message from the actually configured `kennzeichen_format`/`case_number_config.format` values from `object-type-service`/`case-service` (`build_candidate_pattern`) — fallback to the old generic pattern only if one of the two cross-service calls fails.
5. **The body text itself counts as the first (synthetic) attachment** (`{subject}.txt`) — the correspondence itself is just as worth archiving as its attachments.
6. Every part (body text + real attachments) goes through the mandatory virus scan (10.3) via `virus-scan-service`'s `POST /scan` — a clean part is staged under a `posteingang/{message_id}/...` storage key until assignment, an infected one lands exclusively in the already existing quarantine (P15-S2) — no duplicate storage, no special handling here.

## Assignment (`confirm-match`/`assign`)

On confirmation, for each clean, not-yet-assigned attachment, the staged content is read from the Storage Service and created as a new document via the regular `document-service` path (`POST /documents`) — **deliberately not** via the internal quarantine release path from P15-S2 (`POST /documents/from-quarantine-release`): the attachment has already been classified as "clean," a second scan would reproduce the same (positive) result, no structural blocker as with a quarantine release. After successful creation, the staged copy is deleted from the Storage Service. For a case-number match (or manual `case_id` specification), `case-service`'s already existing `POST /cases/{id}/documents` endpoint is additionally called (document reference, 2.3) — no new case-service extension needed. **Since P19-S5** (case-service RBAC, [ADR 0070](../adr/0070-case-service-rbac.md)), `CaseClient` sends a synthetic `X-DMS-Principal: system:mail-connector` header for this as well as for `lookup_by_vorgangsnummer`/`get` (case-service has since checked `case.read`/`case.write`; no human principal exists for this automated assignment path).

## Outbox attachment (`related_document_id`, P24-S3)

Since P24-S3, `POST /outbound` supports an optional file attachment when `related_document_id`
is set — the attachment-side counterpart to the ingestion pipeline (there: incoming attachment → new
document; here: existing document → outgoing attachment). `main.py`'s `_attach_related_document`:

1. Resolves the file metadata of the CURRENT version via `DocumentClient.get_current_version(document_id)`
   — `DocumentOut` itself carries no file metadata (that lives on `DocumentVersionOut` per version in
   document-service, see its `schemas.py`), hence two internal calls: `GET /documents/{id}`
   for `current_version_number`, then `GET /documents/{id}/versions/{version_number}` for
   `filename`/`content_type`/`storage_object_key`. Unknown `related_document_id` → `400`, checked BEFORE
   the SMTP send attempt (same principle as `assign_manually`'s upfront check of an unknown
   `case_id`) — no `OutboundMessage` record is created for a call that is invalid from the outset.
2. Loads the content via the already existing `StorageClient.download(storage_object_key)` — `404` if
   the content is no longer present in the Storage Service (e.g. already disposed of/dehydrated).
3. Attaches it via `EmailMessage.add_attachment(...)` (`maintype`/`subtype` split from the
   `content_type` stored on the document, fallback `application/octet-stream` for a missing/not
   splittable value).

No limit on attachment size beyond what `aiosmtplib`/the configured SMTP server enforces anyway
(no new, mail-connector-specific limit introduced).

## Connection to the backend

- **Storage Service** (3.6): staging of clean attachments/body texts until assignment; downloading the content of a `related_document_id` attachment in the outbox.
- **Virus Scan Service** (10.3): every part of an incoming message is scanned.
- **Document Service**: reference-number lookup (`GET /documents/by-kennzeichen`, new, P15-S3) as well as regular document creation on assignment; since P24-S3 additionally `GET /documents/{id}`/`GET /documents/{id}/versions/{version_number}` for the outbox attachment.
- **Case Service**: case-number lookup (`GET /cases/by-vorgangsnummer`, new) as well as document reference creation (`POST /cases/{id}/documents`, already existing).
- Deliberately **no** `depends_on` on `document-service`/`case-service`/`virus-scan-service` in the reverse direction needed (no cycle, unlike `virus-scan-service`↔`document-service`, P15-S2) — none of these services calls `mail-connector` in turn.

## Events

| Event | Payload |
|---|---|
| `mail_connector.message.received` | `{from_address, subject, status, match_type}` |
| `mail_connector.message.confirmed` | `{confirmed_by, folder_id, manual?}` |
| `mail_connector.message.sent` / `.send_failed` | `{to_address, sent_by}` |

Fall under a new `mail_connector.>` wildcard subject — `audit-service` must add this to its `subjects` (see `docs/services/audit-service.md`).

## Self-registration (Concept 3.2a)

Registers itself with the registry on startup via `dms-registry-client`, same pattern as `virus-scan-service`/`notification-service`.

## Tests

- `uv run pytest services/mail-connector/tests` (**47 tests**, previously 41 — since **Post-Roadmap Phase 31 Session 12a** ([ADR 0123](../adr/0123-multi-inbox-model-env-var-config-no-department-rbac-yet.md)): `test_repository.py` (2 new: `source_uid` scoped per mailbox — two different mailboxes may reuse the same UID without colliding; `list_messages` filters by `mailbox_id`), `test_api.py` (4 new: `GET /mailboxes` requires principal/role, returns the configured default mailbox with no credential fields present, `GET /inbound?mailbox_id=` filters correctly), plus the existing ingestion test extended with a `mailbox_id` assertion; `test_imap_backend.py`'s dedup-contract test updated for the new per-mailbox `get_by_source_uid` signature) — before that, 41 tests, previously 30: `matching.py` (candidate extraction, unambiguous/ambiguous/missing match via fake clients), repository (CRUD for inbound/outbound messages), API (role gate, full ingestion via `_ingest_message` directly instead of via a real POP3 connection — faster/more deterministic, candidate matching against real `document-service`/`case-service` incl. case fixture via the real `workflow-service`, confirm/assign/reject incl. 400/409, outbound send against real `mailpit` incl. file attachment from `related_document_id` — verified via mailpit's own REST API, filename/content-type/bytes of a real uploaded test document), IMAP backend (`test_imap_backend.py`, mocked at the `imaplib` boundary instead of running against a real server — see [ADR 0095](../adr/0095-imap-backend-mocked-imaplib.md) for the rationale: stable composite UID, no repeated deletion/marking on a repeated poll tick, real dedup contract via `repository.get_by_source_uid`, TLS/plain class selection) — the remaining part continues to run against real Postgres AND the real running sibling services, no mocks (same rationale as throughout the project).
- Live verified: real SMTP→POP3 roundtrip against `mailpit` within the running compose stack (see PROGRESS.md); since P24-S3 additionally a real SMTP→IMAP roundtrip against a temporary `greenmail` container as well as a real outbox attachment send (see PROGRESS.md). Since **Post-Roadmap Phase 31 Session 12a**: the ad-hoc migration verified live against the real dev stack's 653 pre-existing messages (all correctly backfilled to `mailbox_id="central"`, the old single-column unique constraint correctly replaced with the composite one), plus a genuine end-to-end SMTP→POP3 roundtrip through the real, rebuilt poll loop confirming a newly arriving message is correctly tagged with `mailbox_id` (see PROGRESS.md).

## Open Points

- ~~**Candidate regex is generic, not derived from the actually configured `kennzeichen_format`/`case_number_config.format` values**~~ — **fixed in Post-Roadmap Phase 19 Session 11** ([ADR 0076](../adr/0076-root-folder-mail-regex-dehydration-409.md)).
- **No bulk rescan of already received, unconfirmed messages on a subsequent format change** — the new format-derived pattern is only applied on the INITIAL ingestion of a message; older messages already sitting as `unassigned` are not retroactively re-checked on a later format change.
- ~~**Only POP3 implemented**~~ — **IMAP built since P24-S3** (`backends/imap_backend.py`, `ImapBackend`). **Microsoft Graph (Exchange/O365) remains deliberately open**: a full Graph OAuth2 client-credentials integration (external app registration, token refresh, Graph REST semantics instead of IMAP/POP3) is a standalone, significantly larger undertaking that doesn't fit into the same session as IMAP + outbox attachments — a deliberate scoping decision for this session. `backends/interface.py`'s `MailboxBackend` interface already supports a future addition following the same pattern as `Pop3Backend`/`ImapBackend` (a new backend only implements `fetch_new_messages`, the rest of the service remains unchanged) — no structural preparation effort needed, only the actual Graph client implementation.
- ~~**Outbox without attachment support**~~ — **fixed in P24-S3**: `POST /outbound` attaches the current content of the referenced document when `related_document_id` is set (see "Outbox attachment" above).
- ~~**Only one mailbox per installation**~~ — **fixed in Post-Roadmap Phase 31 Session 12a**: any number of named mailboxes (see "Multi-Inbox Model" above). **Still open, deliberately deferred to a later session**: per-mailbox/department RBAC (`poststelle_role` stays one global role, every holder sees every mailbox — see ADR 0123 "Consequences"), a frontend mailbox selector (`PoststellePane.tsx` untouched), and admin-UI mailbox management (adding/reconfiguring a mailbox still needs an env var change + restart, consistent with `storage-service`/`signature-service`'s connector *lists* being equally restart-only, ADR 0091). Routing/hand-off between inboxes and the searchable cross-inbox "Postbuch" log are P31-S12b/S12c, not part of this session.
