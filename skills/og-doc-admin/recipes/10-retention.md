# Trash period, deletion reasons, reminders

**Example:** "Papierkorb-Frist auf 60 Tage" / "Löschgrund 'Fehlablage' ergänzen"
**Role:** domain-admin-retention

1. `dms retention show`: both services. Point out if they differ. To align the deletion reasons, offer
   to add the missing ones with `--add-reason` (repeatable). It writes to both services and skips reasons
   a service already has, so nothing is duplicated.
2. **Plan:** the new values.
3. After a yes: `dms retention set --trash-days 60`, or `dms retention set --add-reason "Fehlablage"`.
4. **Verify:** the `show` output of the command.

**Note:** A shorter trash period makes older trashed items permanently deleted sooner. Say so.
