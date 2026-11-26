# Why does X (not) see Y?

**Example:** "Warum sieht Frau X den Ordner Y nicht?"
**Role:** domain-admin-users

1. The user's id (`dms user list`) and the resource id (ask the admin).
2. `dms permission explain <user-id> <resource-id> --permission folder.read`, or `document.read`, or
   `.write` for "can't edit".
3. Explain in plain words, using `references/diagnosis.md`.
4. Offer a fix (assignment, group, inheritance) only if asked. Then the matching recipe.
