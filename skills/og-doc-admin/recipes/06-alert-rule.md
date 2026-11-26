# Alert rule

**Example:** "Alarm, wenn ein Speicher zu 80 % voll ist"
**Role:** domain-admin-monitoring

1. `dms monitoring alert-rule list`: is there a rule already? (`Speicherziel fast voll` ships in
   `packages/monitoring-defaults`.) If yes, show it with its thresholds and current value and level
   (`dms -o json monitoring alert-rule list`), and offer to adjust it instead of adding one. If the admin
   keeps it as it is, you're done: no preview, nothing to apply.
2. `dms monitoring sensors`: pick the sensor (`storage.target.used_percent` for storage fill level).
3. Write the rule:
   `{"name": "...", "sensor": "storage.target.used_percent", "aggregation": "max", "max_warning": 80, "max_error": 90, "warning_repeat": 3, "error_repeat": 2, "description": "..."}`.
4. **Preview:** `dms monitoring alert-rule preview rule.json`. Show the current value and level. If there
   is no value, the sensor may be off or the name wrong.
5. After a yes: `dms monitoring alert-rule create rule.json` (or `update <name> rule.json`).
6. **Verify:** `dms monitoring alerts --all` shows the rule with a level.

**Note:** To install all standard rules at once, use recipe 04 with `packages/monitoring-defaults/config.json`.
