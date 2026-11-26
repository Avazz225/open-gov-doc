# New or changed document/folder type

**Example:** "Neuer Dokumenttyp Bescheid mit Aktenzeichen und Frist" / "Aufbewahrung 10 Jahre"
**Role:** domain-admin-config

1. `dms object-type list`; for a change, `dms -o json object-type get <id>`.
2. Write the file (new) or change only the asked fields (existing). Show it.
3. **Plan:** fields, constraints, retention, and where the type may be filed.
4. After a yes: `dms object-type create -f type.json` or `dms object-type update <id> -f type.json`.
5. **Verify:** `dms object-type get <id>`.

**Note:** `object_type.update` can be under four-eyes. Deleting a type that is in use is refused or irreversible: ask first.
