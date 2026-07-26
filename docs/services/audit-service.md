# audit-service

**Verantwortung:** Unveränderliches, hash-verkettetes Ereignisprotokoll — konsumiert Events aller konfigurierten Producer-Subjects und macht Manipulation nachträglich erkennbar (Konzept 3.4/5.3).

**Konzept-Referenz:** 3.4, 5.3
**Eigenes Postgres-Schema:** `audit` (Tabelle `audit_event`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/events?limit=100` | Aufgezeichnete Ereignisse, chronologisch (aufsteigend) |
| `GET` | `/events/verify` | Prüft Hash-Kette vollständig, meldet `broken_at_id` bei Manipulation |
| `GET` | `/healthz` | Eigener Health-Check |

## Datenmodell

`audit_event`: `id` (PK, autoincrement), `event_id` (unique, für Idempotenz), `event_type`, `occurred_at`, `service_name`, `subject`, `payload` (JSON), `recorded_at`, `prev_hash`, `hash` (unique). `hash = sha256(prev_hash + kanonisches_JSON({event_id, event_type, occurred_at, service_name, subject, payload, recorded_at}))`.

## Events

**Konsumiert:** alle Subjects aus `Settings.subjects` (Default `["registry.>"]`) — aktuell `registry.instance.registered`, `registry.instance.deregistered` vom Registry Service. Neue Producer werden ergänzt, indem ihr Subject-Präfix zur Liste hinzugefügt wird, ohne Code-Änderung am Konsumenten selbst.

**Publiziert:** keine eigenen Events — der Audit Service ist reiner Konsument/Senke.

## Architektur-Entscheidung

Konsument ohne eigenen Stream (`NatsEventBusClient(ensure_stream=False)`) — siehe [ADR 0001](../adr/0001-eventbus-consumer-without-stream-ownership.md). Durable Consumer-Name `audit-service`, kein `deliver_new`, damit nach einem Neustart lückenlos aufgeholt wird (keine Lücke in der Kette).

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- Admin-UI-Sicht auf den Audit-Trail (Konzept 5.3) folgt mit der Admin-UI (P4-S3).
- Export für Prüfungen (CSV/PDF, Konzept 5.4) ist nicht Teil dieser Session.
