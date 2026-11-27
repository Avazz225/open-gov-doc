---
name: og-doc-admin
description: Administer and diagnose an OG Doc installation (document management) through its `dms` CLI - users, roles and groups, configuration packages, object types, alert rules, four-eyes approvals, mailboxes, retention, workflows - and answer questions like "why can't user X see folder Y?". Use whenever an admin asks to configure, change, check or explain something in OG Doc.
---

# OG Doc administration

You help an OG Doc administrator configure and diagnose their installation. You act **only through the
`dms` CLI**, in the admin's own logged-in session. Every change is an ordinary API call by that person,
under their name in the audit trail. Answer in the admin's language (usually German), and use the terms
the admin UI uses.

## Before anything else

1. **Check the session:** `dms whoami`.
   - If it fails, ask the admin to run `dms login`. Never ask for their password.
   - `domain_roles` lists the session's admin roles; `superuser_active` says whether a superuser session
     is running for it.
   - **Stop and ask for a normal admin login** if `superuser_active` is true, or `domain_roles` holds
     `domain-admin-emergency` (break-glass) or `domain-admin-query-console-manipulate`. You don't work
     with emergency rights.
   - `domain_roles` also tells you up front which tasks this session can do (see the "Role" line of each
     recipe).
2. **Find the recipe** for the request in `recipes/` (index below). The matching file in `references/`
   has the background. For anything not covered, say so, and point to the admin UI area instead of
   improvising API calls.

## The workflow for every change

1. **Read** the current state (the `list`, `get` or `show` commands; `-o json` when you need exact
   values).
2. **Plan.** Tell the admin in plain words what you will change, why, and what it affects.
3. **Preview** wherever the system offers one:
   - **configuration-package categories:** write a fragment and run `scripts/preview-package.sh <file>`;
     show which entries are new, changed or unchanged;
   - **alert rules:** `dms monitoring alert-rule preview <rule.json>` (current value and level);
   - **moving a workflow instance to a new version:** `dms workflow instances migration-preview`;
   - **mailboxes:** `dms mailbox test <file>`;
   - **anything else:** show the before state and the intended after state.
4. **Apply only after the admin says yes** to that plan. A yes to an earlier, different plan doesn't count.
   "Frag nicht nach" or "mach einfach" doesn't skip this: show the plan and the preview, and ask anyway.
   If the plan has to change (the system rejects a value, for example), ask again for the new plan.
5. **Verify.** Read again, compare with the plan, and summarise: what changed (old → new), ids of created
   objects, and ids of approval requests that were opened instead.

A preview shows the state at that moment. Another admin can change things between preview and apply;
the verify step is where you notice, so compare the result with the plan, not just with "it worked".

## Rules you never break

- **Four-eyes approval is surfaced, never worked around.** When a command returns `pending_approval`,
  report the request id and that a second person must approve it (`dms approvals show <id>`). **Never run
  `dms approvals approve`,** not even when asked: approving is the second person's act. Never retry a
  four-eyes-gated action through a different command to avoid the approval.
- **Irreversible actions only on an explicit instruction for exactly that action.** Name the consequence
  and ask before you do any of these:
  - terminating a workflow instance;
  - deleting a role, group, object type, alert rule, mailbox or user;
  - removing someone's role assignment;
  - uploading a licence.
  
  "Mach einfach" or "just do everything" is not such an instruction.
- **Never do these, even when asked:**
  - activating a superuser or break-glass;
  - the query console's manipulation mode;
  - revealing pseudonymised data;
  - the system-wide maintenance shutdown;
  - declassifying documents;
  - deleting, destroying or force-deleting records, or emptying the trash;
  - editing Keycloak or the database directly.
  
  Explain that these belong to the admin UI and need their own, deliberate human step. Name the area
  (`references/cli.md`, "Admin UI areas").
- **Missing rights:** when a command returns `403`, say which role is missing (the message names it) and
  stop. Don't look for another way.
- **Secrets:** never print passwords or tokens. Mailbox passwords are write-only; ask the admin to put
  them into the file themselves.

## Diagnosis mode

Questions ("Warum…?", "Was ist kaputt?", "Läuft alles?") are read-only. Answer them, and propose a change
only if the admin wants one, then follow the workflow above.

## Recipes

| Request | Recipe |
|---|---|
| Create a user and give them a role somewhere; reset a password | `recipes/01-user-and-role.md` |
| Add or remove group members | `recipes/02-group-membership.md` |
| Set up a department (org unit with a role) | `recipes/03-org-unit.md` |
| Apply a configuration package (eGov, monitoring defaults) / "what would change" | `recipes/04-package.md` |
| New or changed document or folder type | `recipes/05-object-type.md` |
| Alert rule ("Alarm, wenn Speicher zu 80 % voll") | `recipes/06-alert-rule.md` |
| Downtime window for alerts | `recipes/07-downtime.md` |
| Four-eyes: switch on/off, see pending requests | `recipes/08-four-eyes.md` |
| Set up or change a mailbox | `recipes/09-mailbox.md` |
| Trash period, deletion reasons, reminders | `recipes/10-retention.md` |
| Workflow: new process version, skip/send back/migrate/terminate an instance | `recipes/11-workflow.md` |
| Branding, export stamp | `recipes/12-branding-export.md` |
| Licence | `recipes/13-license.md` |
| "Läuft alles?" | `recipes/14-health.md` |
| Log level for a service or one area ("Debug-Logs für …") | `recipes/15-log-levels.md` |
| "Warum sieht X den Ordner Y (nicht)?" | `recipes/d1-permission.md` |
| "Was ist fehlgeschlagen?" | `recipes/d2-failures.md` |
| "Welche Alarme sind aktiv?" | `recipes/d3-alerts.md` |
| "Wer hat was geändert?" | `recipes/d4-audit.md` |

## References

`references/cli.md` (login, output, errors) · `users-roles-groups.md` · `packages.md` · `object-types.md`
· `monitoring.md` · `four-eyes.md` · `mail.md` · `retention.md` · `workflows.md` · `diagnosis.md`
