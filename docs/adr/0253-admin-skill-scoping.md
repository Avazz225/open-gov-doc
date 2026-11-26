# 0253 — An AI skill for administering OG Doc: scoping

**Status:** accepted (scoping, P96-S1; no code)
**Context:** user request 2026-11-21, Phase 96. An admin should be able to say what they want and get a
safe, explained, reviewable change:
- "Alarm, wenn ein Speicher zu 80 % voll ist"
- "richte das eGov-Paket ein"
- "warum sieht Frau X den Ordner Y nicht?"

OG Doc's configuration is spread over many services:
- 35 areas in admin-ui;
- 19 configuration-package categories;
- 32 domain-admin roles;
- 20 action types that can require four-eyes approval.

The skill should hide that spread without bypassing any of the protections built around it.

## 1. Format and where it ships

- **An Agent Skill:** `SKILL.md` plus `references/`, `recipes/` and `scripts/`, so Claude Code and the
  Agent SDK load it as a skill.
- **Plain Markdown plus shell commands,** so any other agent, or a human, can follow it too. Nothing in it
  depends on a particular agent runtime.
- **In the repository at `skills/og-doc-admin/`,** versioned with the code it describes.
  `scripts/package-skill.sh` builds a zip for releases. A download link in admin-ui is possible later; it
  is not part of Phase 96.

## 2. Tool surface: the `dms` CLI, not an MCP server

The skill acts **only through the `dms` CLI** (`tools/cli`):
- **Same gateway and permissions as the UIs.** The CLI logs in as a person (`dms login`) and every call
  passes the gateway with that person's token. The services' checks (capabilities, four-eyes, maintenance
  mode, licence gate) apply unchanged.
- **Same audit trail.** Every change is an ordinary API call by that person.
- **No new server** to deploy, secure and keep in step.
- **Readable:** a command in a recipe is something a human can run, paste and review.

**An MCP server stays an option** if the skill should run in clients without a shell. It would wrap the
same commands. Not built now.

## 3. Identity and safety

**Whose account:**
- The skill uses the **logged-in admin's own CLI session.** Its changes are audited under that person's
  name, which is exactly where the responsibility lies.
- No shared bot account. The CLI has no service accounts (`docs/tools/cli.md`, Open Points), and a shared
  account would blur who did what.

**Which roles:** that person's domain-admin roles, nothing more.
- **The skill refuses to act when the session holds** `domain-admin-emergency` (break-glass), an active
  superuser session (ADR 0190) or `domain-admin-query-console-manipulate`. It asks the human to use a
  normal admin login instead.
- **The recommended set** for a skill user is written in the customer guide (P96-S3):
  - users;
  - config and config-read;
  - monitoring;
  - mail;
  - retention;
  - folder- and document-config;
  - workflow-instances;
  - licence.

**The workflow for every change: read → plan → preview → apply → verify.**
1. **Read** the current state (`dms … list/get`, `dms config export`).
2. **Plan:** say in plain language what will change, why, and what it affects.
3. **Preview** wherever the system offers one:
   - **configuration categories:** a package fragment, then `dms config compare --base` against the live
     state, then show the diff;
   - **alert rules:** `POST /alert-rules/preview` (how the rule would have fired on recent data);
   - **workflow changes:** the migration preview;
   - **otherwise:** a before/after of the object.
4. **Apply** only after the human says yes. Configuration categories go through `config import`; the rest
   through the direct command.
5. **Verify:** read again and compare with the plan, then give a short summary of what changed (object,
   old → new, the ids of any approval requests).

**Fixed rules:**
- **Four-eyes is surfaced, never worked around.** If an action needs approval, the skill reports the
  request id and who may approve. **It never approves a request itself,** not even when the session could:
  approving is the second person's act.
- **Nothing irreversible without an explicit human instruction for exactly that action.** That covers:
  force-delete; destruction of records (cases, documents); deleting a role, group or object type that is
  in use; terminating workflow instances; the system-wide maintenance shutdown; declassification;
  activating a superuser; licence upload.
  - The skill names the consequence and asks.
  - A general "mach einfach" doesn't count.
- **Never:** the query console's manipulation mode, reading or revealing pseudonymised data
  (`pii-reveal`), and changes to Keycloak outside the CLI.
- **Explain refusals.** When the session lacks a capability, the skill says which role is missing, instead
  of trying another path.

## 4. Task catalogue

The most common admin tasks, from admin-ui, the service docs and the eGov package.

**Path legend:**
- **P:** configuration package (preview with compare, apply with import);
- **D:** direct CLI command;
- **✓:** the CLI covers it today;
- **✗:** gap for P96-S2.

| # | Intent (example) | Role(s) | Path | CLI today |
|---|---|---|---|---|
| 1 | "Leg Frau Müller an und gib ihr Sachbearbeitung im Ordner Bauamt" | users | D: user, role assignment | ✓ / ✓ |
| 2 | "Nimm Herrn Kaya in die Gruppe Poststelle auf" | users | D: group membership | ✗ groups |
| 3 | "Richte die Abteilung Finanzen mit Rolle X ein" | config | P: `org_units`, `org_unit_role_links` | compare ✓, import ✗ |
| 4 | "Richte das eGov-Paket ein" / "Was würde sich ändern?" | config, config-read | P: `packages/egov` | compare ✓, import ✗ |
| 5 | "Neuer Dokumenttyp Bescheid mit Feldern A, B" / "Aufbewahrung 10 Jahre" | config | P `object_types` or D | ✓ (`object-type create/update`) |
| 6 | "Alarm, wenn ein Speicher zu 80 % voll ist" | monitoring | P `alert_rules` or D, with preview | ✗ monitoring (rules, preview, sensors) |
| 7 | "Wartungsfenster Samstag 22–24 Uhr für alle Alarme" | monitoring | P `alert_downtime_windows` or D | ✗ |
| 8 | "Vier-Augen für Rollenzuweisungen einschalten" / "Was liegt zur Freigabe?" | config / – | P `approval_config`; list requests | ✗ (only query-console requests listed) |
| 9 | "Neues Postfach poststelle@… einrichten und testen" | mail | D: mailbox config, test | ✗ mailboxes |
| 10 | "Papierkorb-Frist auf 60 Tage" / "Löschgründe ergänzen" | retention | D retention config; P `deletion_reason_catalogs` | ✗ retention |
| 11 | "Neue Version des Prozesses X einspielen" / "Instanz Y überspringt Schritt Z" | config / workflow-instances | D: process upload, skip, send back, migrate (preview) | list/get ✓; upload, operations ✗ |
| 12 | "Branding: Name und Akzentfarbe" / "Exportstempel einstellen" | config, document-config | P `branding_config`, `export_config` | compare ✓, import ✗ |
| 13 | "Ist die Lizenz gültig, wie viele Nutzer sind frei?" | licence | D | ✓ |
| 14 | "Läuft alles?" (services, instances) | – | D registry | ✓ |

**Diagnosis mode:** read-only. It answers questions, and proposes a fix only if asked.

| # | Question | What it reads | CLI today |
|---|---|---|---|
| D1 | "Warum sieht Frau X den Ordner Y (nicht)?" | the ancestor walk: which node, `inherit`, which assignment (direct, a group, "everyone"), active scope locks, the resulting permission | ✗ **no endpoint explains a decision**; only the result (`effective-permissions`) exists |
| D2 | "Was ist fehlgeschlagen?" and retry | failed renditions, OCR results, notifications, federation handovers, archival transfers | archival ✓; the rest ✗ |
| D3 | "Welche Alarme sind aktiv, was zeigen die Sensoren?" | alert rules with state, sensor values, downtime windows | ✗ |
| D4 | "Wer hat was geändert?" | audit events by actor or subject | ✓ (`query events list`) |
| D5 | Licence, limits, services | licence status, registry | ✓ |

## 5. CLI gaps for P96-S2, in order of need

1. **`dms config import -f <package> [--category]`.** Applies a package and reports
   `applied`/`pending_approval` with the request id. Without it the package path (tasks 3, 4, 12 and every
   P-path) can only preview.
2. **Permission explanation (D1).** A read endpoint in permission-service,
   `GET /explain/{principal}/{resource}?permission=`, returning the walk: per node its id, type, `inherit`,
   matched assignments with their source (direct, a group with its name, "everyone"), role and
   permissions, and active locks; plus the verdict and the deciding step. The CLI command is
   `dms permission explain`. It computes freshly and doesn't touch the cache. The gap is the same for
   admins without the skill.
3. **Groups:** `dms group list|create|members|add|remove` (task 2).
4. **Monitoring:** `dms monitoring sensors`, `alert-rule list|preview|create|update|delete`,
   `downtime list|create|delete`, `alerts` (current state) (tasks 6, 7, D3).
5. **Four-eyes requests:** `dms approvals list [--status] [--action-type]|show <id>` across all action
   types, with `reject` for humans (task 8). `approve` stays a human command; the skill rules forbid it.
6. **Mailboxes:** `dms mailbox list|create|update|delete|test` (task 9).
7. **Retention:** `dms retention show|set` (task 10).
8. **Failures:** `dms failures list [--kind]` and `retry <kind> <id>` (D2).
9. **Workflow:** `dms workflow definitions upload`, `instances skip|send-back|migrate-preview|migrate|terminate`
   with reason (task 11).
10. **Optional:** a client tag. The CLI sends `User-Agent: dms-cli/<v>` plus `og-doc-admin-skill` when the
    skill runs it (`DMS_CLIENT_TAG`), and the access log records it. Then the logs show which changes came
    through the skill, in addition to the audit trail under the admin's name.

**Size:** items 1–5 are the must-haves for the catalogue's core (packages, people, alarms, approvals,
diagnosis). Items 6–9 are each small wrappers over existing endpoints. If one session doesn't hold all of
it, 6–9 move to a P96-S2b, and the skill marks those tasks "über admin-ui" until then.

## 6. Knowledge and how it stays current

**The skill carries condensed references, not the history of 95 phases:**
- **`references/`, one file per domain:** users/roles/groups, configuration packages (the 19 categories,
  order, upsert rules), object types and filing structure, retention and archival, monitoring and alerts,
  mail, workflows (operations, lanes, circulations), four-eyes (action types, who approves), diagnosis.
  Each lists the CLI commands, the required role, the preview available and the irreversible steps.
- **`recipes/`:** one per catalogue task: intent, prerequisites, commands, preview, verification, typical
  errors.
- **`SKILL.md`:** when to use the skill, the safety rules of section 3, the workflow, how to pick a recipe,
  how to refuse.

**Kept current by tests, not by memory:**
- A test in `tools/cli` parses every `dms …` command in `skills/og-doc-admin/**/*.md` and checks it
  against the Typer app (command and options exist). A renamed command fails CI.
- The package reference lists the categories from `config_service.CATEGORIES`, and a test compares the
  two.
- Deeper details link to `docs/services/*.md` instead of copying them.

## 7. Evaluation (P96-S3, outline)

- **A scenario set:**
  - the catalogue tasks;
  - D1–D5;
  - "should refuse or ask" cases: force-delete, approving its own request, "mach einfach alles",
    superuser, a missing role.
- **The run:** each scenario is run with an agent against the dev stack, as a skill user with the
  recommended roles. A scenario passes when the right commands ran, the preview was shown, the change is
  verified, and no rule of section 3 was broken.
- **The skill is iterated** until the set passes. The customer guide covers setup, roles, what the skill
  can and can't do, and how its changes appear in the audit trail.

## Consequences

- P96-S2 is mostly CLI work against existing endpoints, plus one new read endpoint (permission
  explanation) that benefits admins with or without the skill.
- The skill never holds more rights than the person using it. Its safety rests on the services' own
  checks plus the rules above, not on the skill alone.
- Adding an admin area later means a CLI command, a reference entry and a recipe, with the command check
  of section 6 keeping them in step.
