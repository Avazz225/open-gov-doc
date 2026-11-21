# Monitoring default package

Standard alert rules for the sensors OG Doc ships ([ADR 0245](../../docs/adr/0245-sensor-alerting-thresholds-downtime-and-notification.md),
[ADR 0252](../../docs/adr/0252-alert-rules-as-config-category-default-package-and-grafana.md)). An
ordinary configuration package: a configuration document with a `manifest` and the categories
`alert_rules` and `alert_downtime_windows`.

## Applying

It isn't applied automatically. Alarm mails go to the operations mailbox (`operations_admin_email`), and
someone should switch them on knowingly.

- **Admin UI:** `/config-packages/` → select `config.json` → preview → apply.
- **CLI:** `dms config import packages/monitoring-defaults/config.json`.
- **Fleet:** `POST /installations/{id}/provision` with this file as the body.

Applying it again is safe:
- New rules are created.
- Changed rules are updated, by name.
- Unchanged rules are skipped, so streaks that are building up aren't reset.

Rules you created yourself under other names are left alone.

## Contents

| Rule | Sensor | Aggregation | Warning | Error | Repeats (warn/err) |
|---|---|---|---|---|---|
| Speicherziel fast voll | `storage.target.used_percent` | max | > 80 % | > 90 % | 3 / 2 |
| Speicherziel nicht erreichbar | `storage.target.healthy` | min | — | < 0.5 (unreachable) | – / 2 |
| Replikationsrückstand | `storage.replication.backlog` | sum | > 100 | > 1000 | 10 / 10 |
| Replikation endgültig fehlgeschlagen | `storage.replication.failed_permanent` | sum | — | > 0 | – / 1 |
| Dienst-Replikate fehlen | `registry.replicas.state` | min | — | < -0.5 (too few) | – / 2 |
| Zu viele Dienst-Replikate | `registry.replicas.state` | max | > 0.5 (too many) | — | 5 / – |
| Überfällige Heartbeats | `registry.service.heartbeat.miss` | sum | > 0 | > 2 | 2 / 3 |
| Lizenz läuft ab | `license.expiration.days` | min | < 30 days | < 7 days | 1 / 1 |
| Virenfunde in Quarantäne | `virus_scan.quarantined` | sum | > 0 | — | 1 / – |
| OCR-Prüffälle stauen sich | `ocr.results.needs_review` | sum | > 500 | > 2000 | 5 / 5 |
| Benachrichtigungen fehlgeschlagen | `notification.deliveries.failed{state=permanent}` | sum | > 10 | > 100 | 3 / 3 |
| Aussonderungs-Transfers fehlgeschlagen | `archival.transfers.failed` | sum | > 0 | — | 1 / – |
| Papierkorb überfällig | `document.trash.overdue` | sum | > 0 for an hour | — | 60 / – |
| Alte Ausleih-Sperre | `document.locks.oldest_age_hours` | max | > 72 h | — | 1 / – |
| Suchindex-Fehler | `search.index.failures` | rate/min | > 1 | > 10 | 3 / 3 |
| Abgelehnte Anmeldungen | `auth.logins{result=rejected}` | rate/min | > 10 | > 50 | 2 / 2 |
| Knoten-Arbeitsspeicher knapp | `plugin_orchestration.node.available_ram_mb` | min | < 1024 MB | < 256 MB | 3 / 3 |
| Knoten-CPU ausgelastet | `plugin_orchestration.node.cpu_usage_percent` | max | > 85 % | > 95 % | 5 / 10 |
| Prozess-Speicher hoch | `process.memory_bytes` | max | > 1.5 GiB | > 3 GiB | 5 / 5 |

Repeats count evaluations; one happens every 60 s by default. A rule is `unknown` while its sensor has
no value. For example:
- no licence is installed;
- no replica expectation is set;
- no login has been rejected yet;
- the sensor is switched off on the sensors page.

**Downtime window:** "Wartungsfenster Sonntag (Beispiel)", Sundays 02:00–04:00 for every rule, shipped
**disabled**. Enable or adapt it on the sensors page.

The thresholds are starting points. Adjust them on the sensors page; a later re-import of this package
only overwrites a rule whose definition in the package changed.

## Grafana

The alarm states appear without any setup in Grafana. The "Alarme" row of the "DMS – Sensor-Übersicht"
dashboard draws `monitoring_alert_level` (see ADR 0252, Decision 3).
