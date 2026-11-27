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
  - It answers for any id, also one that doesn't exist. Check the user in `dms user list` first: user
    ids are technical ids; if the admin gives a name, look it up, and ask if it's ambiguous.
  - `groups` lists the user's real groups; `everyone` applies to all and isn't listed there.
  - **Typical causes:**
    - no assignment on the path;
    - a folder with inheritance off;
    - the user isn't in the expected group (`groups` in the output);
    - a scope lock (blocks writes, and reads if `blocks_read`);
    - the resource isn't registered (a new object whose registration failed).
  - A note that the cached state differs points to a stale cache. Report it as a defect.
- **"Was ist fehlgeschlagen?"**
  - `dms failures list [--kind rendition|ocr|notification] [--status failed|failed_permanent]
    [--since 24h|7d|<date>] [--limit N] [--group]`, newest first with the time of the last attempt;
    retry with `dms failures retry <kind> <id>` after the admin agrees.
  - Archival transfers: `dms archival transfers list --status failed`.
  - A source you can't read is named with the missing role.
- **"Welche Alarme sind aktiv?"** `dms monitoring alerts` (only warning/error; `--all` for everything),
  with `level`, `value` and `since`.
- **"Wer hat was geändert?"** `dms query events list --actor <user> --since <iso>` or
  `--subject <object-id>`. Role needed: `domain-admin-query-console`.
- **Licence and services:** `dms license status`, `dms registry status`.
- **"Welche Version von X läuft bei uns?" / "Wo steckt Bibliothek Y drin?"** (P100, ADR 0271)
  - `dms components search <name>`: every service that runs it, with version and origin (PyPI, npm,
    Debian, Alpine).
  - `dms components show <service>`: everything in one service; `dms components list` flags replicas
    that differ and instances that differ from their image file.
  - `dms components export -o bom.json`: CycloneDX for an external scanner.
  - Role: `domain-admin-monitoring`.
- **"Sind wir von CVE-… betroffen?"** (P100-S2, ADR 0272)
  - `dms vulnerabilities check CVE-2024-6345`: online, if the installation allows it
    (`DMS_VULNERABILITY_LOOKUP_ENABLED`); only the id leaves the installation.
  - Air-gapped: `--file record.json`, with the OSV record fetched on a connected machine, or
    `--package openssl --ecosystem Debian --fixed 3.3.2-1`.
  - Debian/Alpine use the source package name (`openssl`, not `libssl3t64`).
  - The answer names affected services with the fix version. "Nicht betroffen" lists the installed
    versions outside the range, so the check is visible.
  - OG Doc ships no vulnerability data; a 409 means online lookup is switched off.

