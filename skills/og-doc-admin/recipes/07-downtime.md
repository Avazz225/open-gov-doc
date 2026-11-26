# Downtime window

**Example:** "Samstags 22–24 Uhr keine Alarme"
**Role:** domain-admin-monitoring

1. `dms monitoring downtime list`, and `dms monitoring alert-rule list` for the rule ids.
2. Write the window: `{"name": "...", "weekdays": [5], "daily_start": "22:00", "daily_end": "24:00", "rule_ids": null}`
   (0 = Monday; `null` means every rule).
3. **Plan:** which alerts are silenced, and when.
4. After a yes: `dms monitoring downtime create window.json`.
5. **Verify:** `dms monitoring downtime list` (`active` during the window).

**Note:** A window over every rule hides real outages too. Say so.
