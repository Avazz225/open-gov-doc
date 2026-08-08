# monitoring-service

Sensor-Registry, Sensor-Konfiguration und Prometheus-Scrape-Proxy (Konzept 10.1, P11-S1).
Details siehe [`docs/services/monitoring-service.md`](../../docs/services/monitoring-service.md)
und [`docs/operations/monitoring.md`](../../docs/operations/monitoring.md).

**Architektur (Pull/Proxy, Nutzerentscheidung bei Sessionstart)**: Prometheus scraped
ausschließlich diesen Service. `monitoring-service` fragt `registry-service` nach allen
aktuell aktiven, gesunden Instanzen, ruft deren eigene `/metrics`-Endpunkte live und
parallel ab und führt sie zu einer Antwort zusammen (echtes Federation-Muster über
`prometheus_client.parser`, keine Text-Konkatenation).

**Grenzen dieser Ausbaustufe** (siehe P11-S0-Befund): Pilot an zwei Fach-Services
(`registry-service`, `document-service`), kein Vollretrofit. Sensor-Konfiguration ist
eigenständig persistiert (7.3/Konfigurationsexport existiert erst P12-S3).

## Endpunkte

- `GET /metrics` — Scrape-Ziel für Prometheus, live zusammengeführt.
- `GET /sensors` — aggregierter Sensor-Katalog (Name/Gruppe/Kosten/Beschreibung/Servicetypen/Aktivierungsstatus), ungegatet.
- `GET /sensor-config` — aufgelöste Aktivierungskonfiguration, ungegatet (Poll-Ziel für `dms_metrics_client.SensorConfigClient`).
- `PUT /sensor-config/global` — globale Grundeinstellung setzen (`admin.monitoring` oder aktivierter Superuser).
- `PUT /sensor-config/{sensor_name}` — sensorspezifischen Override setzen/löschen (`admin.monitoring` oder aktivierter Superuser).

## Events

- `monitoring.sensor.config_changed` — bei jeder Konfigurationsänderung, konsumiert von `audit-service` (`monitoring.>`).

## Tests

```bash
uv run pytest services/monitoring-service/tests
```
