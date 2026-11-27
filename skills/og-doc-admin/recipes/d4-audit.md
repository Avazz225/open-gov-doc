# Who changed what?

**Example:** "Wer hat den Dokumenttyp geändert?"
**Role:** domain-admin-query-console

`dms query events list --subject <object-id>`, or `--actor <user-id> --since 2026-11-01T00:00:00Z`.
Summarise who, what and when.
- Newest first, at most 100 events per call. For "zuletzt" the first entry is the answer; narrow with
  `--since` for older questions.
- `403` names the missing role (`domain-admin-query-console`): say so and stop. The admin can look it up
  in the admin UI's „Forensik-Trace".
