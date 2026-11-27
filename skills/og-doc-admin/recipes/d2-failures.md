# What failed?

**Example:** "Was ist fehlgeschlagen?"
**Role:** the roles named in any refusal

1. `dms failures list --group` counts by kind, status and error message, with the latest time per
   group. For "in letzter Zeit" add `--since 24h` (or `7d`, or a date). Also
   `dms archival transfers list --status failed`.
2. Explain the causes (the messages). `dms failures list --since 24h --limit 20` shows single entries,
   newest first, with `when` (the last attempt; for notifications the creation time).
   - `subject` is the document id; name a few if the admin wants to look at them.
   - `--status failed_permanent` shows only what needs a hand.
3. `failed` entries are retried automatically. `failed_permanent` ones only by hand:
   `dms failures retry <kind> <id>`, only after a yes, and only once the cause is gone (a broken source
   file fails again).
