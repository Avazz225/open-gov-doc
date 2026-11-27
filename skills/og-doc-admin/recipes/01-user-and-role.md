# Create a user and give them a role

**Example:** "Leg Frau Müller an und gib ihr Sachbearbeitung im Ordner Bauamt"
**Role:** domain-admin-users

1. `dms user list`: does the user exist already (same username or e-mail)? If so, don't create a second
   one; ask whether the role should go to the existing user.
2. `dms role list`: find the role id. `dms -o json role assignment list --resource <folder-id>` shows
   what is already assigned there.
3. **Plan:** user (username, name, e-mail), role, folder, and that it applies to everything below.
   Ask for the start password if the admin hasn't given one.
4. After a yes:
   - `dms user create --username … --email … --first-name … --last-name … --password …` with the start
     password the admin gave you for the new user. Without `--password` the command prompts, which
     doesn't work from your shell: then hand the admin the full command to run themselves. Never repeat
     the password in your replies or summary;
   - `dms role assignment create --principal-type user --principal <new-user-id> --role <role-id> --resource <folder-id>`.
5. **Verify:** `dms permission explain <user-id> <folder-id> --permission folder.read` shows "ERLAUBT"
   via `direct`. If it says "VERWEIGERT" although the assignment exists, look at the `role` and
   `grants_permission` of that assignment in `-o json`: the role doesn't contain the permission
   (`dms -o json role list` shows each role's permissions). Report that; don't add a second role
   unasked.

**Note:** If the assignment returns `pending_approval`, report the request id: the user exists, but the access follows after approval.

**Password reset** ("Frau Müller hat ihr Passwort vergessen"): `dms user list` for the id, plan, then
after a yes `dms user reset-password <id> --password …` (temporary: she sets her own at the next login).
A `403` means the account has more rights than this session; name them and stop
(`references/users-roles-groups.md`).
