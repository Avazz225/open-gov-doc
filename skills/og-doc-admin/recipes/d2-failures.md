# What failed?

**Example:** "Was ist fehlgeschlagen?"
**Role:** the roles named in any refusal

1. `dms -o json failures list` (and `dms archival transfers list --status failed`). Count by `kind`,
   `status` and error message with `jq`.
2. Group the errors by cause (the message), and explain.
   - The list has no timestamps and no time filter. For "in letzter Zeit" say that you report the whole
     current stock.
   - `subject` is the document id; name a few if the admin wants to look at them.
3. `failed` entries are retried automatically. `failed_permanent` ones only by hand:
   `dms failures retry <kind> <id>`, only after a yes, and only once the cause is gone (a broken source
   file fails again).
