# Active alarms

**Example:** "Welche Alarme sind aktiv?"
**Role:** domain-admin-monitoring

`dms monitoring alerts`: name, sensor, level, value, since. `suppressed` means a downtime window is
active. Explain what each means, using the rule's description (`dms -o json monitoring alert-rule list`).
