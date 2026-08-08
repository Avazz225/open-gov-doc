# Monitoring & Sensor-Konzept (Konzept 10.1, P11-S1/S2)

Konzept 10.1 verlangt ein granular konfigurierbares Sensor-Konzept: jeder Service bringt
benannte, gruppierte, kostenbewusste Messpunkte mit, die sich einzeln oder gruppenweise
aktivieren/deaktivieren lassen — und zwar so, dass ein deaktivierter Sensor **gar nicht
erst Daten erzeugt**, nicht nur unsichtbar im Export bleibt. Diese Seite beschreibt, wie
das im Projekt real umgesetzt ist (`libs/dms-metrics-client`, `registry-service`,
`monitoring-service`) und wie ein weiterer Service mitmacht.

## Architektur im Überblick

```
Prometheus ──scrape──▶ monitoring-service ──scrape──▶ registry-service /metrics
                              │                    └──▶ document-service /metrics
                              │                    └──▶ (weitere Piloten künftig)
                              │
                              └──query──▶ registry-service GET /instances
                                          (Adressen + deklarierte Sensor-Kataloge)
```

**Architekturentscheidung bei Sessionstart** (Rückfrage): Prometheus scraped nicht jeden
Fach-Service direkt, sondern ausschließlich `monitoring-service` (Pull/Proxy-Modell). Die
Fach-Services behalten je einen eigenen `/metrics`-Endpoint; `monitoring-service` kennt über
`registry-service`s bereits vorhandenes `GET /instances` alle aktiven Instanzen, ruft ihre
`/metrics`-Endpunkte live und parallel ab und führt sie zu einer Antwort zusammen (echtes
Federation-Muster über `prometheus_client.parser`, siehe `docs/services/monitoring-service.md`).

Die eigentliche **Sensor-Registry** (Katalog + Ein-/Ausschaltkonfiguration) lebt in
`monitoring-service`, nicht in `registry-service` — Konzept-Domäne "Monitoring", nicht
"Discovery". `registry-service` reicht nur ein zusätzliches `sensors`-Feld auf der
Selbstregistrierung durch.

## Ein Sensor definieren (`libs/dms-metrics-client`)

```python
from dms_metrics_client import SensorRegistry, SensorSpec

SPEC = SensorSpec(
    name="mein_service.irgendwas.dauer",
    group="performance",
    cost="expensive",  # oder "cheap" - Konzept 10.1: jeder Sensor kennt seine Kosten
    description="Was genau gemessen wird",
)

registry = SensorRegistry("mein-service", is_active=config_client.is_active)
sensor = registry.histogram(SPEC)  # oder .counter()/.gauge()
```

- **`counter()`/`histogram()`** werden inline an der Stelle des Ereignisses aufgerufen (`sensor.inc()`/`sensor.observe(wert)`) — bei deaktiviertem Sensor passiert nichts, nicht einmal ein `time.monotonic()`-Aufruf, wenn der Aufrufer vorher `sensor.is_active()` prüft (siehe `document_service.main.create_document`).
- **`gauge()`** für "aktueller Zustand"-Werte wird über `run_gauge_sampler_loop()` periodisch aktualisiert — die zugrunde liegende (ggf. teure) Berechnung läuft nur, wenn der Sensor aktiv ist.
- **`is_active`** kommt entweder von `SensorConfigClient` (TTL-Poll gegen `monitoring-service`, für die meisten Services) oder — falls der Service zufällig selbst `monitoring-service` ist — direkt aus dessen eigenem Zustand.

## Einen Service als Sensor-Quelle anschließen

1. `dms-metrics-client` als Abhängigkeit ergänzen (`pyproject.toml` + `[tool.uv.sources]`).
2. Ein `metrics.py`-Modul mit den `SensorSpec`s des Service anlegen (siehe `registry_service/metrics.py`/`document_service/metrics.py` als Vorbild).
3. In der Lifespan: `SensorConfigClient` starten, `SensorRegistry` bauen, Sensoren an den passenden Stellen aufrufen, `GET /metrics` über `dms_metrics_client.metrics_payload()` exponieren.
4. Bei der Selbstregistrierung (`maybe_start_registration(..., sensors=metrics.sensor_declarations())`) die Sensor-Deklarationen mitschicken — `monitoring-service` liest sie automatisch über `registry-service`s `GET /instances` mit, kein weiterer Schritt nötig.

## Bewusste Vereinfachungen dieser Ausbaustufe

- **Kein Vollretrofit aller 25 Services** (P11-S0-Befund) — nur `registry-service` und `document-service` sind heute Piloten. Weitere Services bekommen ihre Sensoren bei tatsächlichem Bedarf in späteren Sessions, nicht stillschweigend als "erledigt" behauptet.
- **TTL-Poll statt NATS-Invalidierung** für `SensorConfigClient` (Default 15s) — einfacher als das `ComponentLicenseCache`-Vorbild (P9-S2), bewusste Scope-Entscheidung angesichts des Gesamtumfangs dieser Session.
- **Statisches Prometheus-Ziel** (`infra/prometheus.yml`, ein Eintrag `monitoring-service:8000`) statt automatisch aus der Registry abgeleiteter Scrape-Konfiguration — Konzept 10.1 nennt Letzteres als Option, keine Pflicht; mit nur einem Scrape-Ziel bringt die Automatisierung noch keinen Mehrwert.
- **Keine Anbindung an 7.3** (Konfigurationsimport/-export) — der Service existiert erst ab P12-S3 (gleiches Rückwärtsabhängigkeits-Muster wie der P10-S0-Fund zu 10.1). Die Sensor-Konfiguration ist bis dahin eigenständig in `monitoring-service` persistiert und auditiert.
- **Keine Admin-UI-Bedienoberfläche** für Sensor-Ein-/Ausschaltung — nur die Backend-API (`GET /sensors`, `PUT /sensor-config/...`).
- **Kein OpenTelemetry-Tracing angebunden** — `libs/dms-common.configure_tracing()` existiert bereits (vorbereitet, aber von keinem Service aufgerufen), ist aber laut Konzept 10.1 ein eigenständiges, ergänzendes Thema ("Verteiltes Tracing... ergänzt die Sensor-Metriken") und nicht Teil dieser Session.
- **Der Plugin Orchestration Service (P10-S1) wechselt nicht auf diese Sensor-Schicht um** — sein `psutil`-Sampler bleibt bestehen. In der real existierenden Docker-Compose-Umgebung gibt es ohnehin nur einen Host, ein Umstieg brächte vor echter Multi-Node-Infrastruktur keinen Mehrwert (siehe P11-S0-Befund).

## Grafana (Konzept 10.1, P11-S2)

Konzept 10.1: "das System selbst liefert keine eigene Grafana-Ersatz-UI, sondern stellt
sinnvolle Default-Dashboard-Definitionen als exportierbare JSON-Vorlagen bereit (Startpunkt,
keine Pflichtnutzung)." Umgesetzt als:

- **`grafana`-Compose-Service** (`infra/docker-compose.yml`), Default-Login `admin`/`admin`
  (reine Dev-Bequemlichkeit, wie andere Default-Credentials dieses Stacks).
- **Deklarative Provisionierung** — kein manuelles Einrichten in der UI nötig:
  `infra/grafana/provisioning/datasources/prometheus.yml` verdrahtet die bereits laufende
  `prometheus`-Instanz mit fester `uid: prometheus`; `infra/grafana/provisioning/dashboards/dashboards.yml`
  lädt jede JSON-Datei aus `infra/grafana/dashboards/` automatisch beim Start.
- **`infra/grafana/dashboards/dms-sensor-overview.json`** — die vom Konzept geforderte
  exportierbare Vorlage: ein Dashboard mit den vier aktuellen Piloten-Sensoren
  (`registry.instances.active_total`, `registry.service.heartbeat.miss`,
  `document.upload.duration`, `document.count.active_total`) plus dem immer-aktiven
  `monitoring_scrape_failures_total`-Zähler und dem `up{job="monitoring-service"}`-Zielstatus.
  Weitere Sensoren aus künftigen Services (kein Vollretrofit, siehe oben) erscheinen erst
  hier, sobald sie über `monitoring-service` gescraped werden — die Datei selbst bleibt dabei
  unverändert exportierbar/importierbar in jede andere Grafana-Instanz, unabhängig von diesem
  Compose-Stack.

## CheckMK — bewusst nicht Teil dieser Ausbaustufe (P11-S2)

Konzept 10.1 nennt CheckMK als drittes mögliches Anbindungsziel (neben Prometheus/Grafana),
über dessen Standard-Prometheus-Special-Agent oder ein eigenes Check-Plugin. **Rückfrage bei
Sessionstart**: der Nutzer entschied sich explizit gegen eine CheckMK-Anbindung in dieser
Session ("ich will grafana kein checkmk") — weder als laufender Container noch als reine
Dokumentation. Das ist eine bewusste, vom Nutzer getroffene Scope-Entscheidung, keine
technische Notwendigkeit: Recherche vor der Rückfrage ergab, dass ein echter
`checkmk/check-mk-raw`-Container ohne größere manuelle Einrichtung lauffähig wäre (offizielles
Docker-Image, automatische Site-Erstellung), die vollautomatische Konfiguration der
Prometheus-Special-Agent-Regel über die REST-API aber mangels unabhängig dokumentiertem
Rohformat für diesen Ruleset-Typ voraussichtlich einen einmaligen manuellen GUI-Schritt
erfordert hätte. Bleibt als offener Punkt für eine mögliche spätere Session, falls tatsächlich
Bedarf an einer klassischen IT-Monitoring-Landschaft (Alarmierung/Eskalation/SLA-Reporting)
entsteht — nicht stillschweigend als erledigt markiert.

## Sensor-Konfiguration ändern

```bash
# Globale Grundeinstellung (Default: alles aktiv)
curl -X PUT http://localhost:8026/sensor-config/global \
  -H "x-dms-principal: <principal>" -H "Content-Type: application/json" \
  -d '{"enabled": false}'

# Einzelnen Sensor unabhängig von der Grundeinstellung aktivieren
curl -X PUT http://localhost:8026/sensor-config/document.upload.duration \
  -H "x-dms-principal: <principal>" -H "Content-Type: application/json" \
  -d '{"enabled": true}'

# Override wieder löschen (fällt zurück auf die Grundeinstellung)
curl -X PUT http://localhost:8026/sensor-config/document.upload.duration \
  -H "x-dms-principal: <principal>" -H "Content-Type: application/json" \
  -d '{"enabled": null}'
```

Erfordert die Domain-Admin-Rolle `domain-admin-monitoring` (`admin.monitoring`) oder den
aktivierten Superuser. Jede Änderung wird als `monitoring.sensor.config_changed` auditiert.
