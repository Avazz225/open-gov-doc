# Workflow: new process version, instance operations

**Example:** "Neue Version des Antragsprozesses" / "Instanz X überspringt die Prüfung"
**Role:** domain-admin-config (upload), domain-admin-workflow-instances (operations)

**New version:** `dms workflow definitions list`, then `dms workflow definitions upload antrag.bpmn --name <family>`.
Running instances stay on their version. Migrate them only on request:
`dms workflow instances migration-preview <id> --to <new-definition-id>`, then `migrate` with `--reason`.
Operations that take `--expected-version` want the instance's `workflow_version` field from
`dms -o json workflow instances get <id>` (it guards against changes in between).

**Instance operations:**
1. `dms workflow instances get <id>` and `dms workflow instances tasks <id>`.
2. **Plan:** which operation and why; it is logged and claimants are notified.
3. After a yes: `skip <id> <task-id> --reason …`, or `send-back <id> --to <task-id> --reason …`.
4. **Verify:** `dms workflow instances tasks <id>`.

**Terminate** (ends an instance for good):
1. `dms workflow instances get <id>`: status, definition, open tasks.
2. Name the consequence: the instance ends as `terminated`, open tasks are withdrawn, it can't be
   resumed. Ask, even if the request already said "beenden", unless the admin confirmed exactly this
   instance and the consequence.
3. After an explicit yes for that instance: `dms workflow instances terminate <id> --reason "…"` (the
   reason is logged; use the admin's words).
4. **Verify:** `dms workflow instances get <id>` shows `terminated`.
