# Active alarms

**Example:** "Welche Alarme sind aktiv?"
**Role:** domain-admin-monitoring

`dms monitoring alerts`: name, sensor, level, value, since. `suppressed` means a downtime window is
active. Explain what each means, using the rule's description (`dms -o json monitoring alert-rule list`).
- A level is reached when the value is **above** the threshold (`max_warning`/`max_error`), or below a
  `min_…` threshold.
- A rule without a description: explain it from its sensor and thresholds, and mention that it has none.
- Two rules on the same sensor with different thresholds: point it out; changing them is recipe 06.
- Where the cause is handled: `references/cli.md`, "Admin UI areas".
