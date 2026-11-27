# Is everything running?

**Example:** "Läuft alles?"
**Role:** domain-admin-monitoring (alerts), domain-admin-notification-read (failed notifications)

1. `dms registry status`: services and instances.
2. `dms monitoring alerts`: active alarms.
3. `dms failures list --group`: failed processing, counted by cause (see recipe d2), and
   `dms archival transfers list --status failed`.
4. Summarise briefly: what is fine, what needs attention, and where it is handled
   (`references/cli.md`, "Admin UI areas").
