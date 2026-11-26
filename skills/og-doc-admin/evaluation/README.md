# Evaluating the skill

`scenarios.md` is the scenario set (P96-S3). To run it against an installation:

1. **A skill user** with the recommended roles from `docs/guides/admin-assistent.md`, **without**
   `domain-admin-query-console` (scenario D4 checks how a missing role is handled).
2. **Test objects:**
   - `<folder>`: a folder with inheritance switched off and no assignment for the principal `kaya`;
   - `<instance>`: a running workflow instance that may be terminated;
   - `<approval>`: any pending approval request;
   - `<mailbox-file>`: a mailbox JSON with working test credentials (the dev stack's mailpit POP3:
     host `mailpit`, port 1110, the user from `infra/mailpit-pop3-auth`).
3. **The run:** for each scenario group, an agent that:
   - loads `SKILL.md`;
   - acts only through the `dms` CLI logged in as the skill user (no direct API or database access);
   - uses the admin answers from the table when the skill asks;
   - reports every command, question and final reply.
4. **Judge** each report against the "Passes when" column, fix the skill where it fell short, and run
   the affected scenarios again.

Results of the runs are recorded in `docs/adr/0253-admin-skill-scoping.md` ("Evaluation").
