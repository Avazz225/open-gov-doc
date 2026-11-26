# What failed?

**Example:** "Was ist fehlgeschlagen?"
**Role:** the roles named in any refusal

1. `dms failures list` (and `dms archival transfers list --status failed`).
2. Group the errors by cause (the message), and explain.
3. Retry (`dms failures retry <kind> <id>`) only after a yes, and only if the cause is gone.
