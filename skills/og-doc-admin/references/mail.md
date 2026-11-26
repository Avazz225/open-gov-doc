# Mailboxes (mail room)

- **What it is:** mail-connector polls mailboxes (POP3 or IMAP) into the "Poststelle" inbox. `central`
  mailboxes are open to every mail-room user; `departmental` ones only to a group's members
  (`owning_group_id`).
- **Commands:** `dms mailbox list|test <file>|create <file>|update <id> <file>|delete <id>`. Role needed:
  `domain-admin-mail`.
- **The file:**
  - `id` (lowercase, digits, `-`/`_`), `name`, `kind`, `owning_group_id`;
  - `inbound_protocol`, `host`, `port`, `username`, `password`, `use_tls`, `imap_mailbox`, `enabled`.
- **Passwords:** write-only. `list` never shows them, and `update` without a password keeps the stored
  one. Let the admin write the password into the file; don't echo it.
- **Always `test` first.** It checks the connection without saving. Create or update only after a
  successful test or the admin's explicit wish.
- **Deleting** a mailbox stops polling. The messages already fetched stay. Ask first.
