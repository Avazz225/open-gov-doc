# Apply a configuration package / "what would change"

**Example:** "Richte das eGov-Paket ein" / "Was würde das Paket ändern?"
**Role:** domain-admin-config-read (preview), domain-admin-config (import)

1. `scripts/preview-package.sh packages/egov/config.json`: per category, the counts of entries.
   `only_in_compare` = new from the package, `differing` = would change, `identical` = unchanged,
   `only_in_base` = exists only in the installation and stays (an import never deletes).
2. `dms -o json config compare packages/egov/config.json` for the field-level details. Summarise the
   important changes, especially roles, four-eyes settings and alert rules.
3. **Plan:** which categories, what changes. Packages are additive (nothing is deleted).
4. After a yes: `dms config import packages/egov/config.json`, optionally with `--category …` to apply
   only part.
5. **Verify:** compare again; `differing` and `only_in_compare` should be 0 for the imported
   categories. Workflows and DMN that are `identical` aren't imported again; changed ones become a new
   version.

**Note:** `pending_approval`: report the request id, nothing is applied yet. Entry errors: show them; the rest was applied.
