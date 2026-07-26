# registry-service

**Verantwortung:** Service Discovery — Registrierung, Heartbeat, aktive Routingtabelle je Servicetyp (Konzept 3.2a). Lizenzvermittlung (3.2b) ist bewusst noch nicht implementiert, folgt mit dem License Service (Phase 9).

**Konzept-Referenz:** 3.2a
**Eigenes Postgres-Schema:** `registry` (Tabelle `service_instance`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/instances` | Registrieren/Aktualisieren (Upsert nach `instance_id`) |
| `POST` | `/instances/{instance_id}/heartbeat` | Heartbeat, aktualisiert `last_heartbeat_at` |
| `DELETE` | `/instances/{instance_id}` | Deregistrieren |
| `GET` | `/instances/{service_type}` | Nur aktuell erreichbare Instanzen dieses Typs |
| `GET` | `/instances` | Alle Instanzen inkl. berechnetem `healthy`-Flag |
| `GET` | `/healthz` | Eigener Health-Check |

## Datenmodell

`service_instance`: `instance_id` (PK), `service_type`, `version`, `capabilities` (JSON-Liste), `health_endpoint`, `address`, `registered_at`, `last_heartbeat_at`. `healthy` ist kein gespeichertes Feld, sondern wird bei jeder Abfrage aus `last_heartbeat_at` vs. `heartbeat_timeout_seconds` (Default 15s, konfigurierbar über `DMS_HEARTBEAT_TIMEOUT_SECONDS`) berechnet.

## Events

Publiziert (Stream `registry`, `dms-eventbus-client`, nach Commit):

- `registry.instance.registered` — `subject`=`instance_id`, `payload`={`service_type`, `version`}
- `registry.instance.deregistered` — `subject`=`instance_id`, `payload`={`service_type`}

Kein Event pro Heartbeat. Konsumiert wird dieser Strom aktuell vom Audit Service (`docs/services/audit-service.md`).

## Sensoren (Konzept 10.1)

Noch keine — Monitoring/Sensor-Konzept folgt in Phase 11.

## Offene Punkte

- Aktives Anpingen des gemeldeten `health_endpoint` (statt reinem Heartbeat-Push) als mögliche spätere Ergänzung, nicht Teil dieser Session.
- Lizenzvermittlung (3.2b) folgt mit dem License Service.
