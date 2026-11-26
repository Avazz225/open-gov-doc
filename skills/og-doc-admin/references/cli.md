# The `dms` CLI

- **Login:** `dms login` asks for user and password, or takes `--username`, `--password` and
  `--gateway-url`.
  - The admin logs in themselves; you never handle the password.
  - `dms whoami` shows the user and their realm roles; `dms logout` ends the session.
- **Output:** tables by default. `dms -o json <command>` gives JSON. The `-o` goes **before** the
  subcommand. Use JSON when you need exact values or ids.
- **Files:** commands that create or update complex objects take a JSON file. Write it, show it to the
  admin, then pass it (`dms object-type create -f type.json`, `dms monitoring alert-rule create rule.json`).
- **Every call** goes through the gateway with the admin's token. The services check rights,
  maintenance mode, licence and four-eyes exactly as for the admin UI.

## Errors

| Answer | Meaning | What to do |
|---|---|---|
| `401` / "Nicht angemeldet" | session expired | ask the admin to `dms login` |
| `403` "Fehlende Domain-Admin-Rolle '…'" | the session lacks that role | name the role, stop |
| `404` | wrong id or name | list first, then retry with the right id |
| `409` | conflict: changed meanwhile, not running, already exists | read again, explain, ask |
| `422` | invalid input; the message says what | fix the file, show the fix |
| `503` "Wartungsmodus" | maintenance mode is on | stop, tell the admin |
| `pending_approval` | four-eyes: nothing changed yet | report the request id, see `four-eyes.md` |

## Ids

- **Users:** Keycloak ids (`dms user list`). Role assignments and the permission explanation use these
  ids, not the user names.
- **Roles:** numeric ids (`dms role list`).
- **Groups:** commands accept the name or the id.
- **Folders and documents:** their ids from the admin UI or user UI (URL, details panel). Ask the admin
  for them if needed. The root of the filing structure is `root`.
