# monitoring-service

**Verantwortung:** Sensor-Registry (Katalog + Aktivierungskonfiguration) und Prometheus-Scrape-Proxy (Konzept 10.1). Neuer Service seit P11-S1, entstanden aus einer Rückfrage bei Sessionstart: statt dass Prometheus jeden Fach-Service direkt scraped, scraped Prometheus ausschließlich `monitoring-service` — der wiederum die `/metrics`-Endpunkte aller aktuell aktiven, gesunden Instanzen live abfragt und zusammenführt (Pull/Proxy-Modell).

**Konzept-Referenz:** 10.1
**Eigenes Postgres-Schema:** `monitoring` (Tabelle `sensor_config`)

## Architektur

- **Scrape-Proxy, nicht Scrape-Ziel-Liste**: `GET /metrics` fragt `registry-service`s bestehendes `GET /instances` ab (kein neuer Registry-Endpunkt nötig — das `sensors`-Feld auf `InstanceOut` reicht), ruft dann parallel (`asyncio.gather`) die `/metrics`-Endpunkte aller aktiven, gesunden Instanzen ab und führt die Ergebnisse zusammen.
- **Echtes Federation-Muster, keine Text-Konkatenation**: `scraper.merge_metric_families` parst jede gescrapte Antwort mit `prometheus_client.parser.text_string_to_metric_families`, gruppiert nach Metrikname und versieht jede Sample mit einem `instance`-Label. Bloßes Aneinanderhängen roher Exposition-Texte mehrerer Instanzen würde bei gleichnamigen Metriken die Gruppierungsregel des Exposition-Formats verletzen (HELP/TYPE müssen für einen Metriknamen zusammenhängend stehen).
- **Live, nicht gecacht**: jeder `GET /metrics`-Aufruf scraped frisch — kein Hintergrund-Poll-Loop nötig, der Scrape passiert bedarfsgerecht genau dann, wenn Prometheus tatsächlich abfragt.
- **Ein fehlschlagendes Ziel blockiert die anderen nicht**: `scraper.scrape_targets` sammelt Fehler statt abzubrechen und erhöht den immer-aktiven, nicht konfigurierbaren Zähler `monitoring_scrape_failures_total` (eigene `CollectorRegistry`, getrennt von den gescrapten Fach-Service-Metriken — ein Selbstbezug wäre hier unnötige Komplexität ohne echten Nutzen, siehe "Sensor-Katalog" unten).

## Sensor-Registry (10.1)

- **Katalog**: `GET /sensors` aggregiert die Selbstdeklarationen aller aktuell aktiven Instanzen (`instance.sensors`, siehe `registry-service`) nach Sensor-Name, inkl. `service_types`-Liste und aufgelöstem `active`-Status. Bei widersprüchlichen Deklarationen (unwahrscheinlich, da ein Sensor i. d. R. vom selben Servicetyp kommt) gewinnt die zuletzt gesehene Deklaration — dokumentierte Vereinfachung.
- **Konfiguration**: `sensor_config`-Tabelle mit einem Sonderschlüssel `__global__` (Grundeinstellung "alles"/"nichts überwachen", Default `true`) und beliebig vielen sensorspezifischen Overrides. Auflösung: Override vorhanden → dessen Wert, sonst globale Grundeinstellung.
- **Seit P12-S3 an 7.3 (Konfigurationsexport) angebunden**: `config-service` exportiert/importiert `sensor_config` als reguläre Kategorie (`GET /sensor-config` bzw. `PUT /sensor-config/global`+`PUT /sensor-config/{sensor_name}`), siehe [`docs/services/config-service.md`](config-service.md). Löst den bei P11-S0/P11-S1 bewusst zurückgestellten Befund auf — bis dahin war die Konfiguration eigenständig persistiert und auditiert, ohne Anbindung an einen Export/Import-Mechanismus.
- **Erste gegatete Schreiboperation außerhalb der bisherigen Registry-Domäne**: `PUT /sensor-config/global`/`PUT /sensor-config/{sensor_name}` verlangen `admin.monitoring` (neue Domain-Admin-Rolle `domain-admin-monitoring`, `permission-service`) oder den aktivierten Superuser — Konzept 10.1 nennt das Deaktivieren sicherheitsrelevanter Sensoren selbst ausdrücklich als sicherheitsrelevanten Vorgang.

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/metrics` | Scrape-Proxy — das einzige Ziel, das Prometheus tatsächlich abfragt. |
| `GET` | `/sensors` | Aggregierter Sensor-Katalog (Name/Gruppe/Kosten/Beschreibung/Servicetypen/Aktivierungsstatus), ungegatet. |
| `GET` | `/sensor-config` | Aufgelöste Aktivierungskonfiguration (`global_default`, `overrides`), ungegatet — Poll-Ziel für `dms_metrics_client.SensorConfigClient`. |
| `PUT` | `/sensor-config/global` | Globale Grundeinstellung setzen (`admin.monitoring` oder aktivierter Superuser), auditiert. |
| `PUT` | `/sensor-config/{sensor_name}` | Sensorspezifischen Override setzen (`enabled: bool`) oder löschen (`enabled: null`, fällt zurück auf die Grundeinstellung), auditiert. |
| `GET` | `/healthz` | Eigener Health-Check |

## Events

Publiziert (Stream `monitoring`, `dms-eventbus-client`, nach Commit):

- `monitoring.sensor.config_changed` — `subject`=Sensorname oder `"__global__"`, `payload`={`enabled`, `actor`}

Konsumiert von `audit-service` (`monitoring.>`).

## Pilot-Sensoren (kein Vollretrofit, P11-S0-Befund)

Nur `registry-service` und `document-service` deklarieren heute Sensoren:

- `registry.instances.active_total` (Gauge, `capacity`, `cheap`)
- `registry.service.heartbeat.miss` (Gauge, `reliability`, `cheap` — Konzept-10.1-Beispielname)
- `document.upload.duration` (Histogram, `performance`, `expensive` — Konzept-10.1-Beispielname)
- `document.count.active_total` (Gauge, `capacity`, `cheap`)

Weitere Services bekommen ihre Sensoren erst bei tatsächlichem Bedarf in späteren Sessions (`libs/dms-metrics-client` steht dafür bereits bereit, siehe `docs/operations/monitoring.md`).

## Grenzen dieser Ausbaustufe

- **Statisches Prometheus-Ziel** (`infra/prometheus.yml`, ein Eintrag: `monitoring-service:8000`) statt automatisch aus der Registry abgeleiteter Scrape-Konfiguration (10.1 nennt das als Option, keine Pflicht) — mit nur einem Ziel bringt die Automatisierung noch keinen Mehrwert.
- **TTL-Poll statt NATS-Invalidierung** für `SensorConfigClient` (Default 15s) — bewusst einfacher als das `ComponentLicenseCache`-Vorbild (P9-S2), da diese Session bereits Sensor-Konzept + neuer Service + Scrape-Proxy + zwei Piloten umfasst.
- **Kein Admin-UI-Bedienfeld** für Sensor-Ein-/Ausschaltung — nur die Backend-API, gleiches Muster wie andere Sessionen, die Backend vor Frontend liefern.
- **Kein CheckMK** — Konzept 10.1 nennt es als drittes Anbindungsziel, Nutzer entschied sich bei der P11-S2-Planfreigabe explizit dagegen (siehe `docs/operations/monitoring.md`).

## Grafana (P11-S2)

`GET /metrics` ist die einzige Quelle, die Grafana je erreicht — indirekt über Prometheus, das wiederum ausschließlich diesen Service scraped. Ein Default-Dashboard (`infra/grafana/dashboards/dms-sensor-overview.json`) visualisiert alle aktuell deklarierten Sensoren, deklarativ provisioniert (kein manuelles Einrichten). Details siehe `docs/operations/monitoring.md`.

## Tests

```bash
uv run pytest services/monitoring-service/tests
```
