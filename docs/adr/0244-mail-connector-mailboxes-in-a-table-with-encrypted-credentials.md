# 0244 — mail-connector: mailboxes in a table, credentials encrypted at rest

**Status:** accepted
**Context:** P88-S4a (Phase 88). Supersedes the "env-var/restart-only" part of
[ADR 0123](0123-multi-inbox-model-env-var-config-no-department-rbac-yet.md).

Since P31-S12a the mailboxes have been a JSON list in `DMS_MAILBOXES`. Adding a departmental mailbox or
changing a password meant an env change and a restart. ADR 0123 kept them out of the database for
the reason ADR 0091 gave for storage targets: credentials in a live-editable store need encryption and
masking, plaintext must never appear in a `GET`, and that infrastructure didn't exist. The plan for
Phase 88 asks for exactly that infrastructure, so admins can manage mailboxes in the admin UI.

## Decision 1 — A `mailbox` table; one connection block per mailbox

`mail_connector.mailbox` holds:
- `id`, `name`, `kind`, `owning_group_id`;
- `inbound_protocol`, `host`, `port`, `username`, `password_encrypted`, `use_tls`, `imap_mailbox`;
- `enabled`, `created_at`/`updated_at`/`updated_by`.

A mailbox has one protocol, so the protocol-specific `pop3_*`/`imap_*` pairs of `MailboxConfig` collapse
into one block. The runtime still uses `MailboxConfig`: `mailboxes.to_config` converts, and its
validator still checks kind/group and completeness, so the rules of ADR 0123/0132 hold unchanged.

**Every reader reads the table:**
- the poll loop, once per tick, so a change takes effect without a restart;
- the department RBAC (`_accessible_mailbox_ids`, `_require_mailbox_access`);
- `GET /mailboxes` and the routing-target check.

## Decision 2 — The password is encrypted at rest and write-only

- **Encryption:** AES-256-GCM with a random nonce, the key from `DMS_MAILBOX_CREDENTIALS_KEY` (base64,
  32 bytes). This is the same shape as the pseudonymization vaults (ADR 0156), duplicated per service
  as there.
- **Write-only:** the API never returns the password, only `has_password`. On a write, an omitted or
  empty password keeps the stored one. That way the admin page can edit a mailbox's host without
  knowing its secret, and the connection test (Decision 4) can use the stored secret too.
- **Without a key** the service still starts, so the rest of the mail room keeps working:
  - polling of the affected mailboxes fails, logged per mailbox;
  - writes with a password answer `503`.
- **A changed key** makes the stored passwords unreadable. A write then answers `409` "Passwort neu
  eingeben" rather than storing garbage.
- **Deployment:**
  - Compose has a dev default.
  - In Helm, the key sits in the service's env like archival-service's
    `DMS_ARCHIVE_ENCRYPTION_KEY`, with a dev value that must be overridden. Moving such keys into a
    generated Secret is a chart-wide gap, not specific to this service.

## Decision 3 — `DMS_MAILBOXES` is adopted once, then ignored

- **Adoption:** on the first start after the upgrade, the table is empty and `mailbox_adoption` has no
  row. The list from `DMS_MAILBOXES` (or the settings default, the dev mailpit mailbox) is written into
  the table with encrypted passwords and `updated_by = system:adopted-from-env`, and the marker row is
  written.
- **After that** the env is ignored, with a warning on every start while it is still set. The marker
  makes the adoption one-time: without it, an installation whose admin deleted every mailbox would get
  the env list back on the next restart.
- **Concurrency:** two replicas adopting at once collide on the primary keys; the loser rolls back.
- **Helm** now sets `DMS_MAILBOXES: "[]"`, so a fresh cluster adopts nothing (rather than the
  localhost dev default). The chart's former `DMS_POP3_HOST` line had been ignored since P31-S12a and
  is gone.

## Decision 4 — Admin API, its own capability, a connection test

`admin.mail_config` comes with a new domain-admin role `domain-admin-mail` ("Postfach-Konfiguration").
- Managing mailboxes hands out access to every incoming letter, which makes it neither the mail room's
  job (`dms-poststelle` processes the intake) nor the notification templates' admin's.
- **Endpoints:**
  - `GET /mailbox-config`: everything but the password, plus the message count and this replica's last
    poll result (time, ok, error text).
  - `POST`, `PUT /mailbox-config/{id}`: the id is immutable; `422` with the validator's text for
    incomplete or contradictory configs.
  - `DELETE /mailbox-config/{id}`: only without stored messages, `409` otherwise.
- **Disable instead of delete:** `enabled=false` stops polling, but the mailbox stays a name, an RBAC
  scope and a routing source/target for its messages.
- **`POST /mailbox-config/test`:** logs in with a draft (falling back to the stored password of the
  same id) without reading or changing a message, through a new `MailboxBackend.check()`. POP3
  `STAT`, IMAP read-only `SELECT`, 10 s timeout. A failure is a result (`ok: false`, the server's
  reply), not an error.
- **Events:** `mail_connector.mailbox.created`/`updated`/`deleted`, with the admin as actor and no
  secret in the payload. audit-service already consumes `mail_connector.>`.

## Consequences

- mail-connector:
  - `crypto.py` and `mailboxes.py`;
  - the `Mailbox`/`MailboxAdoption` models and the admin endpoints;
  - `_poll_tick` split out of `_poll_loop`, and `backend_factory`/`poll_status` on `app.state`;
  - `cryptography` as a dependency.
- permission-service: `domain-admin-mail`.
- Compose: `DMS_MAILBOX_CREDENTIALS_KEY`. Helm: `DMS_MAILBOXES: "[]"` plus the key.
- The admin page follows in P88-S4b.
