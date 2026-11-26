# Workflow: new process version, instance operations

**Example:** "Neue Version des Antragsprozesses" / "Instanz X überspringt die Prüfung"
**Role:** domain-admin-config (upload), domain-admin-workflow-instances (operations)

**New version:** `dms workflow definitions list`, then `dms workflow definitions upload antrag.bpmn --name <family>`.
Running instances stay on their version. Migrate them only on request:
`dms workflow instances migration-preview <id> --to <new-definition-id>`, then `migrate` with `--reason`.

**Instance operations:**
1. `dms workflow instances get <id>` and `dms workflow instances tasks <id>`.
2. **Plan:** which operation and why; it is logged and claimants are notified.
3. After a yes: `skip <id> <task-id> --reason …`, or `send-back <id> --to <task-id> --reason …`.
4. **Verify:** `dms workflow instances tasks <id>`.

**Note:** `terminate` ends an instance for good. Only on an explicit instruction for that instance.
