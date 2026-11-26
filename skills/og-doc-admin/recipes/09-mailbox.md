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

**Only testing** ("Funktionieren diese Zugangsdaten?"): steps 1 and 3, then report `ok` or the error.
Create nothing. Don't open or print the file. If it fails, say what the server said (wrong password,
host unreachable) and ask the admin to fix the file. `dms mailbox test` exits 0 also on a failed test;
read `ok`.

**Note:** Never print the password. Delete a mailbox file you wrote once it's no longer needed; leave a
file the admin gave you alone.
