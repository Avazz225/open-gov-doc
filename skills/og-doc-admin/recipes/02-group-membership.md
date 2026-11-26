# Add or remove group members

**Example:** "Nimm Herrn Kaya in die Gruppe Poststelle auf"
**Role:** domain-admin-users

1. `dms group list` and `dms group members <name>`: does the group exist, and is the person in it
   already?
2. `dms user list`: the person's id.
3. **Plan:** person, group, and what the group grants (`dms role assignment list --principal <group-id>`).
4. After a yes: `dms group add <group> <user-id>`, or `dms group remove <group> <user-id>`.
5. **Verify:** `dms group members <group>`.

**Note:** Removing someone takes away what the group gave them right away. Say that before you do it.
