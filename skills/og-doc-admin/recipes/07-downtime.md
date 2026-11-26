# Downtime window

**Example:** "Samstags 22–24 Uhr keine Alarme"
**Role:** domain-admin-monitoring

1. `dms monitoring downtime list`, and `dms monitoring alert-rule list` for the rule ids.
2. Write the window: `{"name": "...", "weekdays": [5], "daily_start": "22:00", "daily_end": "00:00", "rule_ids": null}`
   - `weekdays`: 0 = Monday … 6 = Sunday;
   - the end time is exclusive. An end before the start runs past midnight and counts for the start's
     weekday, so "bis 24 Uhr" is `"daily_end": "00:00"` (`24:00` is rejected);
   - `rule_ids`: the ids of the rules to silence; `null` or `[]` means every rule.
   - times are the installation's local time (by default Europe/Berlin); say „Ortszeit der Installation"
     in the plan.
3. **Plan:** which alerts are silenced, and when.
4. After a yes: `dms monitoring downtime create window.json`.
5. **Verify:** `dms -o json monitoring downtime list` (the table leaves out `rule_ids`; `active` is true
   only during the window).

**Note:** A window over every rule hides real outages too. Say so.
