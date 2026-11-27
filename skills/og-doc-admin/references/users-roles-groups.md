# Users, roles, groups

- **Users** live in Keycloak, managed through auth-service: `dms user list|create|delete|reset-password`.
  - `create` needs `--username --email --first-name --last-name`, and asks for a password if
    `--password` isn't given. The prompt doesn't work from your shell: pass `--password` with the start
    password the admin gives you for the new user (ask for it in the plan), or hand the admin the
    command to run. Never repeat it afterwards.
  - `reset-password <id> --password …` sets a **temporary** password by default: the user must choose
    their own at the next login. `--permanent` skips that, so use it only when the admin asks.
    - Refused (`403`) when the account holds rights the session lacks; the message names them. Say so,
      and don't look for another way.
    - Refused for one's own account (`409`).
    - Audited as `auth.user.password_reset`, never with the password.
    - The password-at-prompt rule from `create` applies.
  - Role needed: `domain-admin-users`.
- **Roles** are permission bundles in permission-service (`dms role list`). Domain-admin roles
  (`domain-admin-*`) grant administrative capabilities; the rest grant content permissions
  (`folder.read`, `document.write`, ...).
  - Creating or changing roles goes through the `roles` package category (`packages.md`).
- **Role assignments** bind a role to a user or group **on a resource** (a folder, a case, `root` for
  everything):
  - `dms role assignment create --principal-type user|group --principal <id> --role <role-id> --resource <id>`;
  - `dms role assignment list --principal <id>` or `--resource <id>`;
  - `dms role assignment delete <assignment-id>`. Removing access is irreversible for the user's work:
    ask.
- **Inheritance:**
  - an assignment applies to the resource and everything below it;
  - a folder with "Vererbung aus" (`inherit=false`) stops inheritance from above;
  - the group "everyone" holds the default read/write role at `root`.
- **Groups** (`dms group list|create|members|add|remove|delete`): members get the group's role
  assignments.
  - **Org units** are groups marked `--org-unit`; packages can link roles to them (`org_unit_role_links`).
  - Group names are matched by name in lane bindings and circulation routes.
- **Four-eyes:** `permission.role_assignment.create/delete` and `permission.role.create/update` can be
  under four-eyes. Then the command returns `pending_approval` (`four-eyes.md`).
- **Explaining access:** `dms permission explain <user-id> <resource-id> --permission <perm>`
  (`diagnosis.md`).
