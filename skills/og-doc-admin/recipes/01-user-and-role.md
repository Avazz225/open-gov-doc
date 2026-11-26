# Create a user and give them a role

**Example:** "Leg Frau Müller an und gib ihr Sachbearbeitung im Ordner Bauamt"
**Role:** domain-admin-users

1. `dms user list`: does the user exist already (same username or e-mail)?
2. `dms role list`: find the role id. `dms -o json role assignment list --resource <folder-id>` shows
   what is already assigned there.
3. **Plan:** user (username, name, e-mail), role, folder, and that it applies to everything below.
4. After a yes:
   - `dms user create --username … --email … --first-name … --last-name …` (the admin types the password
     at the prompt);
   - `dms role assignment create --principal-type user --principal <new-user-id> --role <role-id> --resource <folder-id>`.
5. **Verify:** `dms permission explain <user-id> <folder-id> --permission folder.read` shows "ERLAUBT"
   via `direct`.

**Note:** If the assignment returns `pending_approval`, report the request id: the user exists, but the access follows after approval.
