# Diagnosis

Read-only. Answer the question, and propose a fix only if asked.

- **"Warum sieht X den Ordner Y (nicht)?"**
  - Run `dms permission explain <user-id> <resource-id> --permission folder.read`, or `document.read`
    for documents and `.write` for changes. Role needed: `domain-admin-users`.
  - It shows each node from the resource up to the root:
    - whether it inherits;
    - which assignments match there, and how: `direct`, `group:<name>`, `everyone`;
    - which of them grant the permission;
    - active scope locks;
    - the verdict and the deciding step.
  - **Typical causes:**
    - no assignment on the path;
    - a folder with inheritance off;
    - the user isn't in the expected group (`groups` in the output);
    - a scope lock (blocks writes, and reads if `blocks_read`);
    - the resource isn't registered (a new object whose registration failed).
  - A note that the cached state differs points to a stale cache. Report it as a defect.
- **"Was ist fehlgeschlagen?"**
  - `dms failures list [--kind rendition|ocr|notification]`; retry with `dms failures retry <kind> <id>`
    after the admin agrees.
  - Archival transfers: `dms archival transfers list --status failed`.
  - A source you can't read is named with the missing role.
- **"Welche Alarme sind aktiv?"** `dms monitoring alerts` (only warning/error; `--all` for everything),
  with `level`, `value` and `since`.
- **"Wer hat was geändert?"** `dms query events list --actor <user> --since <iso>` or
  `--subject <object-id>`. Role needed: `domain-admin-query-console`.
- **Licence and services:** `dms license status`, `dms registry status`.
