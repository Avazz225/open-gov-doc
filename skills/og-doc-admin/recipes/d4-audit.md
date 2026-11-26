# Who changed what?

**Example:** "Wer hat den Dokumenttyp geändert?"
**Role:** domain-admin-query-console

`dms query events list --subject <object-id>`, or `--actor <user-id> --since 2026-11-01T00:00:00Z`.
Summarise who, what and when.
- At most 100 events per call. For "zuletzt", narrow with `--since` and sort by the timestamp yourself
  (`-o json`); the list isn't guaranteed to be newest first.
- `403` names the missing role (`domain-admin-query-console`): say so and stop. The admin can look it up
  in the admin UI's „Forensik-Trace".
