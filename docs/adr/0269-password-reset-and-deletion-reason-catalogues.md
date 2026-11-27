# 0269 — Password reset by an admin; the two deletion-reason catalogues

**Status:** accepted (P98-S3)
**Context:** two admin gaps from Phases 96/97:
- there was no way to reset another account's password; in P97-S2 the evaluation account's password
  was lost and a new probe account had to be created;
- the deletion-reason catalogues of document- and folder-service differed in the dev stack (none
  vs. four), and `dms retention set` could only add.

## Decision 1 — Password reset, with a no-escalation rule

`POST /users/{id}/password` in auth-service (`admin.user_management`), plus `dms user reset-password`
and a row action in the admin UI's user management.

- **Temporary by default.** Keycloak then requires a new password at the next login, so the admin who
  set it doesn't keep knowing it. `temporary: false` / `--permanent` / „Dauerhaft" only on purpose.
- **No escalation.** Refused (`403`) when the target holds root permissions the acting admin lacks; the
  message names them. Without this rule a user admin could reset a config admin's (or the evaluation
  account's) password and log in with it. Compared through permission-service's effective permissions
  at the root, so everything an account can do counts, whether from a direct role, a group or
  "everyone".
- **Not for one's own account** (`409`); that belongs to the account page.
- **Audited** as `auth.user.password_reset` (`user_id`, `temporary`, `reset_by`), never with the password.

Rejected:
- four-eyes for every reset: too heavy for the common "forgot my password" case. An installation that
  wants it can still gate user management as a whole;
- letting the admin read or choose nothing (a mailed reset link): there is no outgoing reset flow in
  OG Doc yet, and air-gapped installations may have no mail at all.

## Decision 2 — Two catalogues stay, differences are surfaced

Document and folder retention settings stay separate. ADR 0233 decided that documents and folders may
be configured differently, and a different reason list can be intended. Instead:
- `dms retention show` lists a difference below the table;
- `dms retention set` gains `--remove-reason` (both services) and `--align-reasons` (both get the union,
  so nothing is lost);
- the admin UI's „Aufbewahrung & Papierkorb" shows a notice above both sections when they differ, with
  „Beide angleichen". The sections reload afterwards, and the notice follows each save.

## Consequences

- **Live:**
  - a reset (permanent) let the probe log in with the new password; a temporary one didn't until it
    is changed;
  - a reset of `og-skill-admin` by `users-admin` was refused with the missing rights named;
  - the audit trail holds the resets without passwords;
  - the dev stack's catalogue difference is shown in the CLI and the admin UI (1400/390). It wasn't
    aligned: which reasons apply is the admin's decision.
- **Tests:**
  - auth-service: 4 (permanent, temporary, more rights, guards incl. own account);
  - CLI: 2 (reset, align/remove);
  - admin-ui: 4 (reset and refusal, comparison and alignment).
