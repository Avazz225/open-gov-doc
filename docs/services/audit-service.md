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

**Konsumiert:** alle Subjects aus `Settings.subjects` (Default `["registry.>", "document.>", "permission.>", "virus_scan.>", "rendering.>", "ocr.>", "workflow.>", "notification.>", "case.>", "auth.>", "signature.>", "favorite.>"]` — `document.>` seit P3-S2, da 4.2 explizit vollständige Auditierung von Force-Unlock/Konfliktkopie verlangt; `permission.>` seit P3-S4, da 4.7 explizit vollständige Auditierung von Bereichssperren verlangt; `virus_scan.>` seit P5-S1, da 10.3/5.3 explizit die Auditierung von Scan-Ergebnissen verlangen; `rendering.>` seit P5-S2, da erzeugte/gescheiterte Ersatzdarstellungen ebenfalls Teil der nachvollziehbaren Dokumentverarbeitung sind; `ocr.>` seit P5-S3, da 3.9/5.3 explizit die Auditierung von OCR-Ergebnissen verlangen, einschließlich `needs_review`-Fällen; `workflow.>`/`notification.>` seit P6-S1/P6-S2 (Prozessinstanz-/Task-Lebenszyklus bzw. Zustellversuche); `case.>` seit P6-S3 (Umlaufmappen-Lebenszyklus, 2.3); `auth.>` seit **P6-S5**, da 4.6 explizit erhöhte Auditierungspriorität für den Superuser-Break-Glass-Lebenszyklus verlangt (Anforderung/Aktivierung/Deaktivierung — die Priorisierung selbst ist nicht umgesetzt, siehe [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md) "Konsequenzen"); `signature.>` seit **P6-S7**, da elektronische Signaturen (3.10) kryptografisch an eine konkrete Dokumentversion gebunden sind und damit ebenfalls Teil der nachvollziehbaren Dokumentverarbeitung; `favorite.>` seit **P7-S1d**, damit Favoriten-Änderungen wie jede andere Nutzeraktion im Audit-Trail nachvollziehbar bleiben. Neue Producer werden ergänzt, indem ihr Subject-Präfix zur Liste hinzugefügt wird, ohne Code-Änderung am Konsumenten selbst.

**Publiziert:** keine eigenen Events — der Audit Service ist reiner Konsument/Senke.

## Architektur-Entscheidung

Konsument ohne eigenen Stream (`NatsEventBusClient(ensure_stream=False)`) — siehe [ADR 0001](../adr/0001-eventbus-consumer-without-stream-ownership.md). Durable Consumer-Name `audit-service`, kein `deliver_new`, damit nach einem Neustart lückenlos aufgeholt wird (keine Lücke in der Kette).

**Zwischenfall & Fix (P3-S2)**: Mit einem zweiten konsumierten Subject (`document.>` neben `registry.>`) können NATS-Callbacks für unterschiedliche Subjects nebenläufig ausgeführt werden. `append_event` liest den aktuellen Kettenkopf (`prev_hash`) vor dem Insert — ohne Serialisierung konnten zwei nebenläufige Aufrufe denselben `prev_hash` lesen und die Kette korrumpieren (aufgedeckt durch `test_consumer_integration.py`, das nach Hinzufügen von `document.>` plötzlich fehlschlug, weil beim Testlauf bereits aufgelaufene `document.*`-Nachrichten parallel zum injizierten `registry.*`-Testereignis verarbeitet wurden). Fix: `consumer.py` serialisiert alle Aufrufe von `append_event` über einen In-Prozess-`asyncio.Lock` je Handler-Instanz — ausreichend, da der Audit Service als Single-Writer für seine eigene Kette konzipiert ist (keine horizontale Skalierung mehrerer Instanzen auf derselben Kette vorgesehen).

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- Admin-UI-Sicht auf den Audit-Trail (Konzept 5.3) folgt mit der Admin-UI (P4-S3).
- Export für Prüfungen (CSV/PDF, Konzept 5.4) ist nicht Teil dieser Session.
