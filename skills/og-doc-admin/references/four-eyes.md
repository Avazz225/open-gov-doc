# Four-eyes approval

- **What it is:** certain action types can require a second person's approval. The first person's action
  then creates an approval request and nothing changes until someone else approves.
- **Which actions:** `dms approvals list --status all` shows requests of every type. The configuration
  per action type is the `approval_config` package category. Action types include:
  - `config.import`;
  - `permission.role_assignment.create`/`.delete`, `permission.role.create`/`.update`;
  - `object_type.update`;
  - `workflow.process_definition.import`;
  - `document.force_delete`, `folder.force_delete`;
  - `auth.superuser.activate`;
  - `document.classification.declassify`;
  - `migration.transfer.start`;
  - AD group mappings, scope locks.
- **Commands:**
  - `dms approvals list [--status pending|approved|rejected|all] [--action-type …]`;
  - `dms approvals show <id>`;
  - `dms approvals reject <id> --reason …` (only when the admin asks).
- **You never approve.** `dms approvals approve` is for the second person, run by them. If the admin asks
  you to approve, explain that the approval must be their own deliberate act, or someone else's.
- **Switching four-eyes on or off** for an action type is a configuration change: an `approval_config`
  fragment, then preview and import.
  - Switching it **off** weakens a protection. Say so plainly and get an explicit yes.
  - That change itself may be under four-eyes too.
