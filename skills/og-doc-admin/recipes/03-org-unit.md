# Set up a department (org unit with a role)

**Example:** "Richte die Abteilung Finanzen mit der Rolle Sachbearbeitung ein"
**Role:** domain-admin-config (import), domain-admin-users (members)

1. `dms -o json config export --category org_units --category org_unit_role_links --category roles`.
2. Write a fragment with the new entries under `org_units` and `org_unit_role_links`. Keep the entry
   format of the export.
3. **Preview:** `scripts/preview-package.sh fragment.json`.
4. After a yes: `dms config import fragment.json`.
5. **Members:** `dms group add Finanzen <user-id>` for each person, after confirming the list.
6. **Verify:** `dms group list` (org unit true), and `dms permission explain` for one member.

**Note:** The role must exist (category `roles`) before the link; the import applies categories in order.
