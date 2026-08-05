# audit-service

**Verantwortung:** Unveränderliches, hash-verkettetes Ereignisprotokoll — konsumiert Events aller konfigurierten Producer-Subjects und macht Manipulation nachträglich erkennbar (Konzept 3.4/5.3).

**Konzept-Referenz:** 3.4, 5.3
**Eigenes Postgres-Schema:** `audit` (Tabelle `audit_event`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/events?limit=100&actor=&subject=&event_type=&since=&until=` | Aufgezeichnete Ereignisse, chronologisch **absteigend** (neueste zuerst, seit P7-S2b — vorher aufsteigend, siehe unten). Alle Filter optional/kombinierbar (5.4b, seit P7-S2) — `actor`/`subject` exakter Treffer, `event_type` exakt oder NATS-Wildcard (`"document.>"`), `since`/`until` filtert auf `occurred_at` |
| `GET` | `/events/verify` | Prüft Hash-Kette vollständig, meldet `broken_at_id` bei Manipulation |
| `GET` | `/healthz` | Eigener Health-Check |

## Datenmodell

`audit_event`: `id` (PK, autoincrement), `event_id` (unique, für Idempotenz), `event_type`, `occurred_at`, `service_name`, `subject`, `payload` (JSON), `actor` (nullable, seit P7-S2 — siehe unten), `recorded_at`, `prev_hash`, `hash` (unique). `hash = sha256(prev_hash + kanonisches_JSON({event_id, event_type, occurred_at, service_name, subject, payload, recorded_at[, actor]}))` — `actor` fließt nur für Zeilen NACH dem Cutover-Punkt ins kanonische JSON ein (siehe "Actor-Feld & Cutover-Versionierung" unten).

`audit_meta`: Singleton-Zeile (`id=1`, gleiches Muster wie `KennzeichenConfig`/`RetentionConfig` anderer Services) — `actor_field_cutover_id` (die `id` der letzten Zeile vor Einführung des `actor`-Feldes).

## Events

**Konsumiert:** alle Subjects aus `Settings.subjects` (Default `["registry.>", "document.>", "permission.>", "virus_scan.>", "rendering.>", "ocr.>", "workflow.>", "notification.>", "case.>", "auth.>", "signature.>", "favorite.>", "folder.>", "reporting.>"]` — `document.>` seit P3-S2, da 4.2 explizit vollständige Auditierung von Force-Unlock/Konfliktkopie verlangt; `permission.>` seit P3-S4, da 4.7 explizit vollständige Auditierung von Bereichssperren verlangt; `virus_scan.>` seit P5-S1, da 10.3/5.3 explizit die Auditierung von Scan-Ergebnissen verlangen; `rendering.>` seit P5-S2, da erzeugte/gescheiterte Ersatzdarstellungen ebenfalls Teil der nachvollziehbaren Dokumentverarbeitung sind; `ocr.>` seit P5-S3, da 3.9/5.3 explizit die Auditierung von OCR-Ergebnissen verlangen, einschließlich `needs_review`-Fällen; `workflow.>`/`notification.>` seit P6-S1/P6-S2 (Prozessinstanz-/Task-Lebenszyklus bzw. Zustellversuche); `case.>` seit P6-S3 (Umlaufmappen-Lebenszyklus, 2.3); `auth.>` seit **P6-S5**, da 4.6 explizit erhöhte Auditierungspriorität für den Superuser-Break-Glass-Lebenszyklus verlangt (Anforderung/Aktivierung/Deaktivierung — die Priorisierung selbst ist nicht umgesetzt, siehe [ADR 0023](../adr/0023-superuser-breakglass-and-domain-admin-accounts.md) "Konsequenzen"); `signature.>` seit **P6-S7**, da elektronische Signaturen (3.10) kryptografisch an eine konkrete Dokumentversion gebunden sind und damit ebenfalls Teil der nachvollziehbaren Dokumentverarbeitung; `favorite.>` seit **P7-S1d**, damit Favoriten-Änderungen wie jede andere Nutzeraktion im Audit-Trail nachvollziehbar bleiben; `folder.>` seit **P7-S2** — echter, beim Live-Smoke-Test dieser Session entdeckter Bestandsfehler: `folder-service` hat bereits seit P7-S1b einen eigenen Event-Stream, wurde aber nie in diese Liste aufgenommen. Nachgeholt inkl. rückwirkendem Backfill der kompletten bisherigen Ordner-Historie (JetStream liefert einem neuen Subject standardmäßig die volle Stream-Historie, kein manuelles Nachziehen nötig). `reporting.>` seit **P7-S2b** vorsorglich ergänzt (damals ohne Wirkung, `reporting-service` publizierte noch keine eigenen Events) — seit **P7-S2c** der erste tatsächliche Producer dieses Streams (`reporting.forensic_trace.queried`, Selbst-Audit des Forensik-Trace-Zugriffs, siehe `docs/services/reporting-service.md`). Neue Producer werden ergänzt, indem ihr Subject-Präfix zur Liste hinzugefügt wird, ohne Code-Änderung am Konsumenten selbst.

**Publiziert:** keine eigenen Events — der Audit Service ist reiner Konsument/Senke.

## Architektur-Entscheidung

Konsument ohne eigenen Stream (`NatsEventBusClient(ensure_stream=False)`) — siehe [ADR 0001](../adr/0001-eventbus-consumer-without-stream-ownership.md). Durable Consumer-Name `audit-service`, kein `deliver_new`, damit nach einem Neustart lückenlos aufgeholt wird (keine Lücke in der Kette).

**Zwischenfall & Fix (P3-S2)**: Mit einem zweiten konsumierten Subject (`document.>` neben `registry.>`) können NATS-Callbacks für unterschiedliche Subjects nebenläufig ausgeführt werden. `append_event` liest den aktuellen Kettenkopf (`prev_hash`) vor dem Insert — ohne Serialisierung konnten zwei nebenläufige Aufrufe denselben `prev_hash` lesen und die Kette korrumpieren (aufgedeckt durch `test_consumer_integration.py`, das nach Hinzufügen von `document.>` plötzlich fehlschlug, weil beim Testlauf bereits aufgelaufene `document.*`-Nachrichten parallel zum injizierten `registry.*`-Testereignis verarbeitet wurden). Fix: `consumer.py` serialisiert alle Aufrufe von `append_event` über einen In-Prozess-`asyncio.Lock` je Handler-Instanz — ausreichend, da der Audit Service als Single-Writer für seine eigene Kette konzipiert ist (keine horizontale Skalierung mehrerer Instanzen auf derselben Kette vorgesehen).

## Actor-Feld & Filter-API (5.4b-Voraussetzung, seit P7-S2)

**Ausgangslage**: der handelnde Nutzer steckte bislang uneinheitlich unter einem von vielen `payload`-Schlüsseln (`deleted_by`, `created_by`, `initiated_by`, `approved_by`, `set_by`, ...) statt first-class im Event — für die geplante Forensik-Trace (5.4b: "alle Aktionen von Nutzer X") reicht das rohe Append-only-Log so nicht aus. Der gemeinsame `Event`-Umschlag (`libs/dms-eventbus-client`) bekam deshalb ein neues `actor: str | None`-Feld, das **jeder** Producer-Service (13 Services, 71 Aufrufstellen) beim Publizieren befüllt — Nutzername, wo ein Mensch die Aktion ausgelöst hat, sonst `"system:<komponente>"` (z. B. `"system:retention-poll"`, `"system:ocr-service"` — Wiederverwendung der bereits vor P7-S2 etablierten Konvention). Ein Konsument, der als Reaktion auf ein fremdes Event selbst wieder etwas publiziert (z. B. `case-service` bei `workflow.instance.completed`), reicht `event.actor` des auslösenden Events weiter, statt einen neuen Wert zu erfinden — dieselbe kausale Handlung, dieselbe handelnde Person. Für einige Aufrufstellen ohne jede vorhandene Aktions-Identität (z. B. `document.metadata.updated`, `folder.resource.moved`/`.deleted`, `document.restored`) bleibt `actor` bewusst `None` — diese Session hat nur bereits vorhandene Angaben first-class gemacht, keine neuen Felder in fremden Schemata ergänzt.

**Cutover-Versionierung statt Neuberechnung der Historie**: das bloße Hinzufügen eines neuen Feldes zum kanonischen Hash-JSON hätte **jede** historische Verkettungsprüfung rückwirkend gebrochen (das kanonische JSON unterscheidet sich bereits durch den zusätzlichen Schlüssel, selbst bei `actor: None`). Deshalb hält `audit_meta.actor_field_cutover_id` (einmalig beim ersten Start nach der Migration auf `MAX(id)` der zu diesem Zeitpunkt bestehenden Zeilen gesetzt, `0` bei leerer Kette) fest, ab welcher `id` das Feld gilt — `_hashable_fields()` schließt `actor` nur für `id > cutover_id` ein, sowohl beim Anhängen neuer Zeilen als auch beim Nachrechnen in `verify_chain`. Alte Zeilen bleiben dadurch mit exakt demselben Feldsatz verifizierbar, mit dem sie ursprünglich gehasht wurden — `GET /events/verify` blieb nach der Migration nachweislich `ok: true` mit identischer Zeilenzahl (Live-Smoke-Test).

**Filter-API** (`GET /events`): `actor`/`subject` exakter Treffer, `event_type` exakt oder NATS-Wildcard-Konvention (`"document.>"` → SQL `LIKE 'document.%'`, dieselbe Notation wie `Settings.subjects`), `since`/`until` auf `occurred_at`. Rein additive Query-Parameter am bestehenden Endpunkt, kein neuer Endpunkt nötig — es gab bislang keinen Frontend-Konsumenten, der hätte brechen können.

**Sortierreihenfolge korrigiert (seit P7-S2b)**: `list_events` sortierte ursprünglich nach `id` aufsteigend vor dem `LIMIT` — bei einer breiten, kaum gefilterten Abfrage lieferte das die **ältesten** Treffer statt der jüngsten. Erst der `reporting-service`s Nutzeraktivitäts-Bericht (erster Aufrufer ohne enges `since`/`until`-Zeitfenster) deckte das auf: eine gerade erst durchgeführte Aktion fehlte im Ergebnis, obwohl sie mit einem `actor`-Filter auffindbar war. Fix: `order_by(id.desc())` — liefert seither die neuesten Treffer zuerst, kein bestehender Test/Konsument pinnte die alte Reihenfolge fest.

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

- `uv run pytest services/audit-service/tests`: Hash-Chain-Grundfunktionen (`test_hashchain.py`, unverändert), Repository inkl. neuer Cutover-Tests (Zeitpunkt-Berechnung, Idempotenz, Verifikation über die Cutover-Grenze hinweg mit manuell als "Alt-Zeile" konstruierten Einträgen) und Filter-Kombinationen (`actor`/`subject`/`event_type` exakt+Wildcard/Zeitfenster), Consumer-Integrationstest gegen echtes NATS inkl. `actor`-Roundtrip. **21 Tests** (vorher 12, 9 neu, seit P7-S2).
- **Live-Smoke-Test** (P7-S2): `GET /events/verify` vor und nach der Migration verglichen — `ok: true` mit identischer geprüfter Zeilenzahl für die Alt-Historie, siehe `PROGRESS.md`. Neue Ereignisse (z. B. `POST /folders`) tauchten korrekt mit `actor` auf, `GET /events?actor=...`/`?event_type=folder.>&since=...` lieferten die erwarteten Treffer, ein systemausgelöstes Ereignis zeigte `actor="system:retention-poll"`.

## Offene Punkte

- Admin-UI-Sicht auf den Audit-Trail (Konzept 5.3) folgt mit der Admin-UI (P4-S3).
- **Export für Prüfungen (CSV/PDF) und Standardberichte (5.4a) folgen in P7-S2b** (neuer Reporting Service, Read-Modell über den Event-Strom) — diese Session liefert nur die dafür nötige first-class Actor-/Filter-Grundlage.
- **Forensik-Trace-UI (5.4b) folgt in P7-S2c** — baut direkt auf der hier gebauten Filter-API auf (kompromittierter Account: "alle Aktionen von Nutzer X ab Zeitpunkt Y").
- **`actor` bleibt an einigen Aufrufstellen `None`**, da die jeweiligen Schemata bislang keine Aktions-Identität tragen (z. B. `document.metadata.updated`, `folder.resource.moved`/`.deleted`, `document.restored`/`.retention.updated`) — Nachrüsten dieser Felder war bewusst nicht Teil von P7-S2 (reiner First-class-statt-ad-hoc-Retrofit bereits vorhandener Angaben, keine neuen Felder in fremden Schemata).
- **Keine Rollenprüfung für `GET /events`/`GET /events/verify`** — jeder mit Netzwerkzugriff auf das Gateway kann den vollständigen Audit-Trail lesen, identische, bereits bestehende Lücke wie zuvor.
