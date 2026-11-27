# Trash period, deletion reasons, reminders

**Example:** "Papierkorb-Frist auf 60 Tage" / "Löschgrund 'Fehlablage' ergänzen"
**Role:** domain-admin-retention

1. `dms retention show`: both services; a difference in the deletion reasons is listed below the table.
   Point it out and offer `--align-reasons` (both get the union, nothing is lost).
2. **Plan:** the new values.
3. After a yes: `dms retention set --trash-days 60`, `dms retention set --add-reason "Fehlablage"`,
   `dms retention set --remove-reason "Dublette"`, or `dms retention set --align-reasons`.
4. **Verify:** the `show` output of the command.

**Note:** Removing a reason only takes it off the suggestion list; existing deletions keep the reason
they were given.

**Note:** A shorter trash period makes older trashed items permanently deleted sooner. Say so.
