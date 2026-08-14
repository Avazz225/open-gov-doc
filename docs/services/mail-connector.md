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
| `POST` | `/outbound` | Postausgang — sendet eine externe E-Mail (SMTP), protokolliert Erfolg/Fehlschlag; `sent_by` kommt aus `X-DMS-Principal`, nicht aus dem Body. Bei gesetzter `related_document_id` wird der aktuelle Inhalt des referenzierten Dokuments als Datei-Anhang mitgeschickt (400 bei unbekannter `related_document_id`, geprüft VOR dem Versandversuch) |
| `GET` | `/outbound` | Liste versandter Nachrichten |
| `GET` | `/healthz` | Health-Check |

Alle Endpunkte außer `/healthz` verlangen `X-DMS-Principal` (401 ohne) und die konfigurierte Poststelle-Rolle (403 ohne).

## Abhol-Protokoll (3.3, Connector-Prinzip)

`backends/interface.py`s `MailboxBackend` ist wie die Storage-Backends/Virenscan-Engines pluggbar (`DMS_INBOUND_PROTOCOL`, `"pop3"` (Default) und seit P24-S3 `"imap"` implementiert). `Pop3Backend` (`backends/pop3_backend.py`) nutzt Pythons Standardbibliothek `poplib` — kein zusätzliches Paket nötig. `poplib` ist synchron; jeder Aufruf läuft über `asyncio.to_thread`.

**Entwicklungsstandard (POP3)**: `mailpit` (bereits als Dev-SMTP-Testserver vorhanden) dient als Selbst-Loopback-Quelle — seit v1.15 bringt es einen eigenen, per `--pop3-auth-file` aktivierbaren POP3-Server mit. Dieselbe per SMTP eingelieferte Mail lässt sich damit über ein echtes Standardprotokoll zurücklesen, ohne einen externen Mailserver zu brauchen (gleiches Prinzip wie der Federation-Hub-Selbst-Loopback, P6-S9). Zugangsdaten (`infra/mailpit-pop3-auth`, dev-only) müssen zu `DMS_POP3_USERNAME`/`DMS_POP3_PASSWORD` passen.

**IMAP (`backends/imap_backend.py`, `ImapBackend`)**: nutzt ebenfalls nur die Standardbibliothek (`imaplib`), gleiches `asyncio.to_thread`-Muster. Holt über den UID-basierten Befehlssatz ab (`UID SEARCH`/`UID FETCH` mit `BODY.PEEK[]`, RFC 3501 §6.4.8) statt der volatilen Sequenznummern — analog zu POP3s `UIDL`. Da eine bloße IMAP-UID nur innerhalb derselben `UIDVALIDITY`-Epoche stabil ist, ist die an `repository.get_by_source_uid` weitergereichte `source_uid` zusammengesetzt: `f"{uidvalidity}:{uid}"`. `select` läuft mit `readonly=True`, der Fetch nutzt `BODY.PEEK[]` statt `RFC822`/`BODY[]` — beides verhindert serverseitige `\Seen`-Seiteneffekte, dieselbe Zurückhaltung wie POP3s bewusst unterlassenes `client.dele()`. IMAP-spezifische Settings: `DMS_IMAP_HOST`/`_PORT`/`_USERNAME`/`_PASSWORD`/`_USE_TLS` sowie `DMS_IMAP_MAILBOX` (Default `INBOX`, IMAP-Ordner sind konfigurierbar — POP3 kennt keine benannten Ordner).

**Kein Entwicklungsstandard-Selbst-Loopback für IMAP**: `mailpit` (Stand `v1.30.6`) hat anders als beim POP3-Fall keinen eigenen IMAP-Server (`docker run axllent/mailpit:v1.30.6 --help` listet kein `--imap*`-Flag). `ImapBackend`s Tests (`tests/test_imap_backend.py`) mocken deshalb an der `imaplib`-Grenze statt gegen einen echten Server zu laufen — siehe [ADR 0095](../adr/0095-imap-backend-mocked-imaplib.md) für die vollständige Begründung und die stattdessen durchgeführte Live-Verifikation gegen einen temporären `greenmail`-Container.

Ein neues Protokoll (z. B. Microsoft Graph für Exchange/O365) implementiert nur `MailboxBackend`, der Rest des Service bleibt unverändert — siehe "Offene Punkte".

## Ingestion-Pipeline (`_ingest_message`, main.py)

1. Poll-Loop (`_poll_loop`, alle `poll_interval_seconds`, Default 20s) ruft `MailboxBackend.fetch_new_messages()` ab.
2. Idempotenz: jede Nachricht trägt eine backend-eigene, stabile `source_uid` (POP3-UIDL) — bereits verarbeitete UIDs werden übersprungen (`repository.get_by_source_uid`).
3. `_parse_message` zerlegt die rohe RFC-822-Nachricht (`email`-Standardbibliothek) in Absender/Betreff/Textkörper/Anhänge.
4. **Matching** (`matching.py`): ein Regex-Kandidaten-Extraktor findet `X-Y`-artige Token (z. B. `2026-001`) in Betreff+Text, prüft jeden Kandidaten gegen `GET /documents/by-kennzeichen` UND `GET /cases/by-vorgangsnummer`. Genau ein Treffer über beide Referenztypen hinweg → `status="proposed_match"`; kein oder mehrdeutiger Treffer → `status="unassigned"` (alle Kandidaten bleiben für die manuelle Zuordnung sichtbar, `match_candidates`). **Seit Post-Roadmap Phase 19 Session 11** ([ADR 0076](../adr/0076-root-folder-mail-regex-dehydration-409.md)) wird das Muster nicht mehr hartkodiert, sondern je eingehender Nachricht frisch aus den tatsächlich konfigurierten `kennzeichen_format`/`case_number_config.format`-Werten von `object-type-service`/`case-service` abgeleitet (`build_candidate_pattern`) — Rückfall auf das alte generische Muster nur, falls einer der beiden Cross-Service-Aufrufe fehlschlägt.
5. **Der Textkörper selbst zählt als erster (synthetischer) Anhang** (`{Betreff}.txt`) — die Korrespondenz an sich ist genauso archivierungswürdig wie ihre Beilagen.
6. Jeder Teil (Textkörper + echte Anhänge) durchläuft den verpflichtenden Virenscan (10.3) über `virus-scan-service`s `POST /scan` — ein sauberer Teil wird bis zur Zuordnung unter einem `posteingang/{message_id}/...`-Storage-Key zwischengelagert, ein infizierter landet ausschließlich in der bereits bestehenden Quarantäne (P15-S2) — keine doppelte Ablage, keine Sonderbehandlung hier.

## Zuordnung (`confirm-match`/`assign`)

Bei Bestätigung wird für jeden sauberen, noch nicht zugeordneten Anhang der zwischengelagerte Inhalt aus dem Storage Service gelesen und über den regulären `document-service`-Pfad (`POST /documents`) als neues Dokument angelegt — **bewusst nicht** über den internen Quarantäne-Freigabe-Pfad aus P15-S2 (`POST /documents/from-quarantine-release`): der Anhang wurde bereits als "clean" eingestuft, ein zweiter Scan reproduziert denselben (positiven) Befund, kein struktureller Blocker wie bei einer Quarantäne-Freigabe. Nach erfolgreicher Anlage wird die zwischengelagerte Kopie aus dem Storage Service gelöscht. Bei einem Vorgangsnummer-Treffer (oder manueller `case_id`-Angabe) wird zusätzlich `case-service`s bereits bestehender `POST /cases/{id}/documents`-Endpunkt aufgerufen (Dokumentreferenz, 2.3) — keine neue case-service-Erweiterung nötig. **Seit P19-S5** (case-service-RBAC, [ADR 0070](../adr/0070-case-service-rbac.md)) sendet `CaseClient` dafür sowie für `lookup_by_vorgangsnummer`/`get` einen synthetischen `X-DMS-Principal: system:mail-connector`-Header (case-service prüft seither `case.read`/`case.write`, kein menschlicher Principal existiert für diesen automatisierten Zuordnungspfad).

## Postausgang-Anhang (`related_document_id`, P24-S3)

`POST /outbound` unterstützt seit P24-S3 einen optionalen Datei-Anhang, sofern `related_document_id`
gesetzt ist — das anhang-seitige Gegenstück zur Ingestion-Pipeline (dort: eingehender Anhang → neues
Dokument; hier: bestehendes Dokument → ausgehender Anhang). `main.py`s `_attach_related_document`:

1. Löst über `DocumentClient.get_current_version(document_id)` die Datei-Metadaten der AKTUELLEN Version
   auf — `DocumentOut` selbst trägt keine Datei-Metadaten (die liegen bei document-service je Version auf
   `DocumentVersionOut`, siehe dortiges `schemas.py`), deshalb zwei Aufrufe intern: `GET /documents/{id}`
   für `current_version_number`, dann `GET /documents/{id}/versions/{version_number}` für
   `filename`/`content_type`/`storage_object_key`. Unbekannte `related_document_id` → `400`, geprüft VOR
   dem SMTP-Versandversuch (gleiches Prinzip wie `assign_manually`s Vorab-Prüfung einer unbekannten
   `case_id`) — kein `OutboundMessage`-Datensatz wird für einen von vornherein ungültigen Aufruf angelegt.
2. Lädt den Inhalt über die bereits bestehende `StorageClient.download(storage_object_key)` — `404`, falls
   der Inhalt im Storage Service nicht (mehr) vorhanden ist (z. B. bereits ausgesondert/dehydriert).
3. Hängt ihn über `EmailMessage.add_attachment(...)` an (`maintype`/`subtype` aus dem am Dokument
   hinterlegten `content_type` gesplittet, Rückfall `application/octet-stream` bei fehlendem/nicht
   aufteilbarem Wert).

Kein Limit auf die Anhanggröße über das hinaus, was `aiosmtplib`/der konfigurierte SMTP-Server ohnehin
durchsetzen (kein neues, mail-connector-eigenes Limit eingeführt).

## Anbindung an das Backend

- **Storage Service** (3.6): Zwischenlagerung sauberer Anhänge/Textkörper bis zur Zuordnung; Herunterladen des Inhalts eines `related_document_id`-Anhangs im Postausgang.
- **Virus-Scan Service** (10.3): jeder Teil einer eingehenden Nachricht wird gescannt.
- **Document Service**: Kennzeichen-Lookup (`GET /documents/by-kennzeichen`, neu, P15-S3) sowie reguläre Dokumentanlage bei Zuordnung; seit P24-S3 zusätzlich `GET /documents/{id}`/`GET /documents/{id}/versions/{version_number}` für den Postausgang-Anhang.
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

- `uv run pytest services/mail-connector/tests` (41 Tests, vorher 30): `matching.py` (Kandidaten-Extraktion, eindeutiger/mehrdeutiger/fehlender Treffer über Fake-Clients), Repository (CRUD für Inbound-/Outbound-Nachrichten), API (Rollen-Gate, vollständige Ingestion über `_ingest_message` direkt statt über eine echte POP3-Verbindung — schneller/deterministischer, Kandidaten-Matching gegen echten `document-service`/`case-service` inkl. Fall-Fixture über den echten `workflow-service`, Confirm/Assign/Reject inkl. 400/409, Outbound-Versand gegen echtes `mailpit` inkl. Datei-Anhang aus `related_document_id` — verifiziert über mailpits eigene REST-API, Dateiname/Content-Type/Bytes eines echten hochgeladenen Testdokuments), IMAP-Backend (`test_imap_backend.py`, an der `imaplib`-Grenze gemockt statt gegen einen echten Server — siehe [ADR 0095](../adr/0095-imap-backend-mocked-imaplib.md) für die Begründung: stabile zusammengesetzte UID, kein erneutes Löschen/Markieren bei wiederholtem Poll-Tick, echter Dedup-Vertrag über `repository.get_by_source_uid`, TLS-/Plain-Klassenwahl) — der übrige Teil läuft weiterhin gegen echtes Postgres UND die echten laufenden Sibling-Services, keine Mocks (gleiche Begründung wie im gesamten Projekt).
- Live verifiziert: echter SMTP→POP3-Roundtrip gegen `mailpit` innerhalb des laufenden Compose-Stacks (siehe PROGRESS.md); seit P24-S3 zusätzlich echter SMTP→IMAP-Roundtrip gegen einen temporären `greenmail`-Container sowie ein echter Postausgang-Anhang-Versand (siehe PROGRESS.md).

## Offene Punkte

- ~~**Kandidaten-Regex ist generisch, nicht aus den tatsächlich konfigurierten `kennzeichen_format`/`case_number_config.format`-Werten abgeleitet**~~ — **behoben in Post-Roadmap Phase 19 Session 11** ([ADR 0076](../adr/0076-root-folder-mail-regex-dehydration-409.md)).
- **Kein Bulk-Rescan bereits eingegangener, unbestätigter Nachrichten bei einer nachträglichen Formatänderung** — das neue formatabgeleitete Muster wird nur bei der ERST-Ingestion einer Nachricht angewendet, bereits als `unassigned` liegende ältere Nachrichten werden bei einer späteren Formatänderung nicht rückwirkend neu geprüft.
- ~~**Nur POP3 implementiert**~~ — **IMAP seit P24-S3 gebaut** (`backends/imap_backend.py`, `ImapBackend`). **Microsoft Graph (Exchange/O365) bleibt bewusst offen**: eine vollständige Graph-OAuth2-Client-Credentials-Anbindung (externe App-Registrierung, Token-Refresh, Graph-REST-Semantik statt IMAP/POP3) ist ein eigenständiges, deutlich größeres Vorhaben, das nicht in dieselbe Session wie IMAP + Postausgang-Anhänge passt — bewusste Zuschnitt-Entscheidung dieser Session. `backends/interface.py`s `MailboxBackend`-Interface unterstützt eine künftige Ergänzung bereits nach demselben Muster wie `Pop3Backend`/`ImapBackend` (ein neues Backend implementiert nur `fetch_new_messages`, der Rest des Service bleibt unverändert) — kein struktureller Vorbereitungsaufwand nötig, nur die eigentliche Graph-Client-Implementierung.
- ~~**Postausgang ohne Anhang-Unterstützung**~~ — **behoben in P24-S3**: `POST /outbound` hängt bei gesetzter `related_document_id` den aktuellen Inhalt des referenzierten Dokuments an (siehe "Postausgang-Anhang" oben).
