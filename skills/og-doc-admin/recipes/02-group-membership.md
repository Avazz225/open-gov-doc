# Add or remove group members

**Example:** "Nimm Herrn Kaya in die Gruppe Poststelle auf"
**Role:** domain-admin-users

1. `dms group list` and `dms group members <name>`: does the group exist, and is the person in it
   already? If the group doesn't exist, say so and ask whether to create it (and what it is for). Don't
   create it on your own, and don't pick a similarly named group. A yes without a purpose is enough:
   create it without a description. A department with its own role is an org unit (recipe 03).
2. `dms user list`: the person's id.
3. **Plan:** person, group, and what the group grants (`dms role assignment list --principal <group-id>`).
4. After a yes:
   - only if the admin agreed to a new group: `dms group create <name> --description …`;
   - `dms group add <group> <user-id>`, or `dms group remove <group> <user-id>`.
5. **Verify:** `dms group members <group>`.

**Note:** A new group grants nothing until it gets a role assignment (recipe 01, with
`--principal-type group`). Say so when you create one.

**Note:** Removing someone takes away what the group gave them right away. Say that before you do it.
