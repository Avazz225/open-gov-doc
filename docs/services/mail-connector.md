# mail-connector

**Verantwortung:** Technischer Empfang/Versand externer Korrespondenz für den Posteingang/Postausgang-Sonderbereich (Konzept 2.5), Sichtung/Zuordnung durch eine dedizierte Poststelle-Rolle, automatisches Vorschlagen einer Zuordnung anhand eines im Betreff/Text gefundenen Kennzeichens (document-service) oder einer Vorgangsnummer (case-service).
**Konzept-Referenz:** 2.5/3.3 (Connector-Prinzip)/7.1 (Zuordnungs-Workflow, hier direkt statt über BPMN)
**Eigenes Postgres-Schema:** `mail_connector` (`inbound_message`, `inbound_attachment`, `outbound_message`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `GET` | `/inbound?status_filter=...` | Liste eingegangener Nachrichten inkl. Anhänge, optional nach `status` gefiltert. Rollen-gegated (`poststelle_role`, Default `dms-poststelle`) |
| `GET` | `/inbound/{id}` | Einzelne Nachricht — 404 unbekannt |
| `POST` | `/inbound/{id}/confirm-match` | Bestätigt einen vorgeschlagenen Treffer (`status="proposed_match"`, sonst 409) — legt für jeden sauberen Anhang (inkl. des Textkörpers, siehe unten) ein Dokument im Ordner des zugeordneten Dokuments (oder `folder_id`, falls angegeben) an; bei Vorgangsnummer-Treffern ist `folder_id` Pflicht (400 ohne) |
| `POST` | `/inbound/{id}/assign` | Manuelle Zuordnung — Pflichtfelder `title`/`folder_id`, optional `case_id` (fügt bei Erfolg eine Dokumentreferenz zur Umlaufmappe hinzu) |
| `POST` | `/inbound/{id}/reject` | Verwirft eine Nachricht (z. B. Spam) — `status="rejected"`, optionaler Grund |
| `POST` | `/outbound` | Postausgang — sendet eine externe E-Mail (SMTP), protokolliert Erfolg/Fehlschlag; `sent_by` kommt aus `X-DMS-Principal`, nicht aus dem Body |
| `GET` | `/outbound` | Liste versandter Nachrichten |
| `GET` | `/healthz` | Health-Check |

Alle Endpunkte außer `/healthz` verlangen `X-DMS-Principal` (401 ohne) und die konfigurierte Poststelle-Rolle (403 ohne).

## Abhol-Protokoll (3.3, Connector-Prinzip)

`backends/interface.py`s `MailboxBackend` ist wie die Storage-Backends/Virenscan-Engines pluggbar (`DMS_INBOUND_PROTOCOL`, aktuell nur `"pop3"` implementiert). `Pop3Backend` (`backends/pop3_backend.py`) nutzt Pythons Standardbibliothek `poplib` — kein zusätzliches Paket nötig. `poplib` ist synchron; jeder Aufruf läuft über `asyncio.to_thread`.

**Entwicklungsstandard**: `mailpit` (bereits als Dev-SMTP-Testserver vorhanden) dient als Selbst-Loopback-Quelle — seit v1.15 bringt es einen eigenen, per `--pop3-auth-file` aktivierbaren POP3-Server mit. Dieselbe per SMTP eingelieferte Mail lässt sich damit über ein echtes Standardprotokoll zurücklesen, ohne einen externen Mailserver zu brauchen (gleiches Prinzip wie der Federation-Hub-Selbst-Loopback, P6-S9). Zugangsdaten (`infra/mailpit-pop3-auth`, dev-only) müssen zu `DMS_POP3_USERNAME`/`DMS_POP3_PASSWORD` passen.

Ein neues Protokoll (z. B. IMAP, Microsoft Graph für Exchange/O365) implementiert nur `MailboxBackend`, der Rest des Service bleibt unverändert.

## Ingestion-Pipeline (`_ingest_message`, main.py)

1. Poll-Loop (`_poll_loop`, alle `poll_interval_seconds`, Default 20s) ruft `MailboxBackend.fetch_new_messages()` ab.
2. Idempotenz: jede Nachricht trägt eine backend-eigene, stabile `source_uid` (POP3-UIDL) — bereits verarbeitete UIDs werden übersprungen (`repository.get_by_source_uid`).
3. `_parse_message` zerlegt die rohe RFC-822-Nachricht (`email`-Standardbibliothek) in Absender/Betreff/Textkörper/Anhänge.
4. **Matching** (`matching.py`): ein generischer Regex-Kandidaten-Extraktor findet `X-Y`-artige Token (z. B. `2026-001`) in Betreff+Text, prüft jeden Kandidaten gegen `GET /documents/by-kennzeichen` UND `GET /cases/by-vorgangsnummer`. Genau ein Treffer über beide Referenztypen hinweg → `status="proposed_match"`; kein oder mehrdeutiger Treffer → `status="unassigned"` (alle Kandidaten bleiben für die manuelle Zuordnung sichtbar, `match_candidates`).
5. **Der Textkörper selbst zählt als erster (synthetischer) Anhang** (`{Betreff}.txt`) — die Korrespondenz an sich ist genauso archivierungswürdig wie ihre Beilagen.
6. Jeder Teil (Textkörper + echte Anhänge) durchläuft den verpflichtenden Virenscan (10.3) über `virus-scan-service`s `POST /scan` — ein sauberer Teil wird bis zur Zuordnung unter einem `posteingang/{message_id}/...`-Storage-Key zwischengelagert, ein infizierter landet ausschließlich in der bereits bestehenden Quarantäne (P15-S2) — keine doppelte Ablage, keine Sonderbehandlung hier.

## Zuordnung (`confirm-match`/`assign`)

Bei Bestätigung wird für jeden sauberen, noch nicht zugeordneten Anhang der zwischengelagerte Inhalt aus dem Storage Service gelesen und über den regulären `document-service`-Pfad (`POST /documents`) als neues Dokument angelegt — **bewusst nicht** über den internen Quarantäne-Freigabe-Pfad aus P15-S2 (`POST /documents/from-quarantine-release`): der Anhang wurde bereits als "clean" eingestuft, ein zweiter Scan reproduziert denselben (positiven) Befund, kein struktureller Blocker wie bei einer Quarantäne-Freigabe. Nach erfolgreicher Anlage wird die zwischengelagerte Kopie aus dem Storage Service gelöscht. Bei einem Vorgangsnummer-Treffer (oder manueller `case_id`-Angabe) wird zusätzlich `case-service`s bereits bestehender `POST /cases/{id}/documents`-Endpunkt aufgerufen (Dokumentreferenz, 2.3) — keine neue case-service-Erweiterung nötig.

## Anbindung an das Backend

- **Storage Service** (3.6): Zwischenlagerung sauberer Anhänge/Textkörper bis zur Zuordnung.
- **Virus-Scan Service** (10.3): jeder Teil einer eingehenden Nachricht wird gescannt.
- **Document Service**: Kennzeichen-Lookup (`GET /documents/by-kennzeichen`, neu, P15-S3) sowie reguläre Dokumentanlage bei Zuordnung.
- **Case Service**: Vorgangsnummer-Lookup (`GET /cases/by-vorgangsnummer`, neu) sowie Dokumentreferenz-Anlage (`POST /cases/{id}/documents`, bereits bestehend).
- Bewusst **kein** `depends_on` auf `document-service`/`case-service`/`virus-scan-service` in der umgekehrten Richtung nötig (kein Zyklus, anders als bei `virus-scan-service`↔`document-service`, P15-S2) — keiner dieser Services ruft `mail-connector` seinerseits auf.

## Events

| Event | Payload |
|---|---|
| `mail_connector.message.received` | `{from_address, subject, status, match_type}` |
| `mail_connector.message.confirmed` | `{confirmed_by, folder_id, manual?}` |
| `mail_connector.message.sent` / `.send_failed` | `{to_address, sent_by}` |

Fallen unter ein neues `mail_connector.>`-Wildcard-Subject — `audit-service` muss dafür in `subjects` ergänzt werden (siehe `docs/services/audit-service.md`).

## Selbst-Registrierung (Konzept 3.2a)

Meldet sich beim Start über `dms-registry-client` selbst bei der Registry an, gleiches Muster wie `virus-scan-service`/`notification-service`.

## Tests

- `uv run pytest services/mail-connector/tests` (30 Tests): `matching.py` (Kandidaten-Extraktion, eindeutiger/mehrdeutiger/fehlender Treffer über Fake-Clients), Repository (CRUD für Inbound-/Outbound-Nachrichten), API (Rollen-Gate, vollständige Ingestion über `_ingest_message` direkt statt über eine echte POP3-Verbindung — schneller/deterministischer, Kandidaten-Matching gegen echten `document-service`/`case-service` inkl. Fall-Fixture über den echten `workflow-service`, Confirm/Assign/Reject inkl. 400/409, Outbound-Versand gegen echtes `mailpit`) — läuft gegen echtes Postgres UND die echten laufenden Sibling-Services, keine Mocks (gleiche Begründung wie im gesamten Projekt).
- Live verifiziert: echter SMTP→POP3-Roundtrip gegen `mailpit` innerhalb des laufenden Compose-Stacks (siehe PROGRESS.md).

## Offene Punkte

- **Kandidaten-Regex ist generisch, nicht aus den tatsächlich konfigurierten `kennzeichen_format`/`case_number_config.format`-Werten abgeleitet** — deckt beide Default-Formate ab, eine Installation mit stark abweichenden Formaten muss `matching._CANDIDATE_RE` anpassen (siehe ADR 0053).
- **Kein Bulk-Rescan bereits eingegangener, unbestätigter Nachrichten bei einer nachträglichen Formatänderung.**
- **Nur POP3 implementiert** — IMAP/Graph-API sind über das `MailboxBackend`-Interface vorbereitet, aber nicht gebaut.
- **Postausgang ohne Anhang-Unterstützung** — `POST /outbound` versendet nur Betreff+Text, keine Datei-Anhänge (könnte einen bereits im DMS liegenden `related_document_id`-Inhalt anhängen, ist in dieser Session nicht umgesetzt).
