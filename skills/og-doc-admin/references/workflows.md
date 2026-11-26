# Workflows

- **Process definitions:**
  - `dms workflow definitions list`, `dms workflow definitions get <id>`;
  - `dms workflow definitions upload <file.bpmn> --name <family>`;
  - the same name gives a new version, and running instances stay on their version;
  - role needed: `domain-admin-config`;
  - the import can be under four-eyes (`workflow.process_definition.import`).
- **Instances:** `dms workflow instances list [--status running] [--definition <id>]|get <id>|tasks <id>`.
- **Administrative operations,** with role `domain-admin-workflow-instances`. Each needs `--reason`, which
  goes into the audit trail and the notice to affected claimants:
  - `skip <instance> <task>`: complete a ready task without its work. Not for signature tasks.
  - `send-back <instance> --to <completed-task-id>`: back to an earlier step.
  - `migration-preview <instance> --to <definition-id>` and then `migrate`: move to a newer version of
    the same process. Always preview first.
  - `terminate <instance>`: end it for good, without completing it. Irreversible: ask for an explicit
    instruction for this instance.
- **Optimistic locking:** pass `--expected-version` (from `instances get`) when you changed something
  based on what you read. A `409` means someone else changed it meanwhile.
- **Circulations** (the "Umlauf" template): their route is edited in the user UI. You don't change routes.
