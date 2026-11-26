# Set up or change a mailbox

**Example:** "Neues Postfach poststelle@amt.de einrichten"
**Role:** domain-admin-mail

1. `dms mailbox list`.
2. Write the file: `id`, `name`, `kind`, `inbound_protocol`, `host`, `port`, `username`, `use_tls`. The
   admin adds `password` themselves.
3. **Preview:** `dms mailbox test mailbox.json` (`ok`, or the error).
4. After a successful test and a yes: `dms mailbox create mailbox.json`, or
   `dms mailbox update <id> mailbox.json`.
5. **Verify:** `dms mailbox list` (`last_poll_ok` after the next poll).

**Note:** Never print the password; delete the file afterwards if it holds one.
