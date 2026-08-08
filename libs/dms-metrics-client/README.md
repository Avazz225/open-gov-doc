# dms-metrics-client

Sensor-Konzept (Konzept 10.1, P11-S1): konfigurierbare, kostenbewusste Prometheus-Sensoren für DMS-Services.

Ein Sensor ist ein `SensorSpec` (Name/Gruppe/Kosten/Beschreibung), registriert über eine
`SensorRegistry`, die daraus `Guarded*`-Wrapper (`GuardedCounter`/`GuardedGauge`/`GuardedHistogram`)
um `prometheus_client`-Objekte baut. Ist ein Sensor deaktiviert, unterbleibt die Erfassung
**vollständig** (kein `inc()`/`observe()`/`set()` auf dem zugrunde liegenden Prometheus-Objekt,
keine teure Datenbankabfrage im periodischen Sampler) — nicht nur die Sichtbarkeit im Export.

Der Aktivierungsstatus kommt von einer `is_active(name) -> bool`-Funktion, die die
`SensorRegistry` beim Bau erhält:

- Für entfernte Services: `SensorConfigClient` pollt `monitoring-service`s `GET /sensor-config`
  (TTL-Cache, Default 15s, fail-open auf "alles aktiv").
- `run_gauge_sampler_loop()` ist ein generischer Poll-Loop für "aktueller Zustand"-Gauges
  (z. B. "wie viele aktive Dokumente gibt es gerade") — ruft die teure Berechnung nur für
  aktuell aktive Sensoren auf.
- `metrics_payload(registry)` liefert rohe Bytes + Content-Type im Prometheus-Exposition-Format
  (kein FastAPI-Abhängigkeit in dieser Lib — der Service baut die `Response` selbst).

Siehe `docs/operations/monitoring.md` für das Gesamtbild (Scrape-Proxy über `monitoring-service`,
Sensor-Registry, Konfigurationsverwaltung) und `services/registry-service`/`services/document-service`
für die zwei Piloten, die diese Lib nutzen.
