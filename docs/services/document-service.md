# document-service

**Verantwortung:** Dokumente als Kernentität (Konzept 2.1) — CRUD, dauerhafte Versionierung (2.1a, kein Überschreiben/Verwerfen), Bearbeitungssperre bei externer Bearbeitung inkl. Force-Unlock und Konfliktkopie (4.2). Hält selbst nie Dateiinhalte — jeder Byte-Zugriff läuft über die HTTP-API des Storage Service (3.6).

**Konzept-Referenz:** 2.1/2.1a/4.2/3.1/3.6/5.2/5.2a (Aufbewahrung/Legal Hold/Zwangslöschung, seit P7-S1)/5.4b (Audit-Tiefe für Forensik-Trace, seit P7-S2c)
**Eigenes Postgres-Schema:** `document` (Tabellen `document`, `document_version`, `document_lock`, `upload_config`, `legal_hold`, `deletion_register_entry`, `retention_config`, `trash_config`, `audit_trace_config`, `audit_trace_role_override`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/documents` | Anlegen (multipart: `file`, `title`, `created_by`, optional `folder_id`/`object_type_id`/`attributes` als JSON-String, optional `derived_from_document_id`/`derived_from_version_number`/`originating_case_id` für Bearbeitungskopien, siehe unten) — erzeugt Dokument + Version 1. Seit P5-S1: `422` bei einem Virenfund, `503` wenn der Virus-Scan Service nicht erreichbar ist (siehe unten). Seit **P5e-S2**: bei konfiguriertem Kennzeichengenerator wird `attributes["Kennzeichen"]` serverseitig vergeben, ein vom Client mitgesendeter Wert für diesen Schlüssel wird verworfen (siehe "Kennzeichengenerator" unten) |
| `GET` | `/documents?folder_id=...` | Nicht gelöschte Dokumente eines Ordners (seit P4-S2, Grundlage der User-UI-Navigation) — unbekannter `folder_id` liefert `[]`, kein 404 |
| `GET` | `/documents/{id}` | Metadaten. Seit **P7-S2c**: publiziert bei Erfolg optional `document.viewed` (Forensik-Trace, 5.4b) — abhängig von der Audit-Tiefe-Konfiguration, siehe unten |
| `PATCH` | `/documents/{id}` | Metadaten nachträglich ändern (`title`/`attributes`, beide optional — seit P4-S4, Grundlage des Metadaten-Panels der User-UI) — bei gesetztem `object_type_id` erneute Validierung gegen den Object-Type Service, sonst 400. Seit **P5e-S2**: eine Änderung an `attributes["Kennzeichen"]` wird mit `403` abgelehnt, außer der `X-DMS-Roles`-Header enthält `dms-admin` (siehe unten) |
| `DELETE` | `/documents/{id}?deleted_by=...` | Weiche Löschung (`deleted_at` gesetzt, Metadaten bleiben) — ungegateter Weg, unverändert seit P7-S1; kein Frontend ruft ihn aktuell auf (siehe `POST .../trash` unten) |
| `POST` | `/documents/{id}/trash` | Löschantrag-Workflow für reguläre Nutzer (`deleted_by`, 5.2, seit P7-S1c) — optional per Vier-Augen-Prinzip gegated (Aktionstyp `document.delete`, unabhängig von `document.force_delete`); Response `TrashResult{status: "trashed"\|"pending_approval", document, approval_request_id}`, exaktes Muster wie `force_release_lock` |
| `GET` | `/documents/deleted?folder_id=...` | Gelöschte (im Papierkorb befindliche) Dokumente eines Ordners (5.2, seit P7-S1) — vor `/documents/{id}` registriert, damit `"deleted"` nicht als `{id}` interpretiert wird |
| `POST` | `/documents/{id}/restore` | Wiederherstellung aus dem Papierkorb (5.2, seit P7-S1) — `404` wenn nicht gelöscht, `409` wenn `TrashConfig.restore_period_days` bereits überschritten ist |
| `PUT` | `/documents/{id}/retention` | Aufbewahrungsfrist/Zwangslöschungs-Flag setzen (`retention_until`, `full_deletion`, optional `reason`/`notify_email`, 5.2/5.2a, seit P7-S1) — `422`, wenn ein Löschgrund Pflicht ist (`RetentionConfig.deletion_reason_required`/`ObjectType.deletion_reason_required_override`) und fehlt |
| `POST` | `/legal-holds` | Legal Hold setzen (`document_id`, `set_by`, optional `reason`, 5.2, seit P7-S1) — überschreibt jede fällige Aktion, bis er aufgehoben wird |
| `POST` | `/legal-holds/{id}/release` | Legal Hold aufheben (`released_by`) |
| `GET` | `/legal-holds?document_id=...&active_only=...` | Legal Holds eines Dokuments |
| `GET` | `/deletion-register?...` | Löschregister lesen (5.2a, seit P7-S1) — eigene, sofort abfragbare API, siehe unten |
| `GET`/`PUT` | `/retention-config` | Installationsweite Aufbewahrungs-Konfiguration (`deletion_reason_required`, `reminder_lead_days`, seit P7-S1) |
| `GET`/`PUT` | `/trash-config` | Papierkorb-Konfiguration (`restore_period_days`, seit P7-S1) |
| `POST` | `/documents/cascade-trash` | Interner Service-zu-Service-Aufruf von `folder-service` (`folder_ids`, `via_folder_id`, `deleted_by`, 5.2, seit P7-S1b) — soft-löscht alle aktiven Dokumente in den angegebenen Ordnern, wenn deren Ordner in den Papierkorb verschoben wird |
| `POST` | `/documents/cascade-restore` | Gegenstück zu `cascade-trash` (`via_folder_id`) — stellt nur die dadurch kaskadiert gelöschten Dokumente wieder her |
| `POST` | `/documents/count-active` | Interner Aufruf von `folder-service` (`folder_ids`) — Nicht-leer-Prüfung vor einer Ordner-Zwangslöschung |
| `GET` | `/documents/{id}/content` | Inhalt der aktuellen Hauptversion. Seit **P7-S2c**: publiziert bei Erfolg optional `document.downloaded` (5.4b), siehe unten |
| `GET` | `/documents/{id}/versions` | Alle Versionen inkl. Konfliktkopien (2.1a: nichts wird je verworfen) |
| `GET` | `/documents/{id}/versions/{n}` | Metadaten einer konkreten Version |
| `GET` | `/documents/{id}/versions/{n}/content` | Inhalt einer konkreten Version. Seit **P7-S2c**: publiziert bei Erfolg optional `document.downloaded` (`version_number` im Payload) |
| `POST` | `/documents/{id}/versions` | Check-in (multipart: `file`, `expected_base_version_number`, `created_by`, optional `comment`) — siehe Konflikterkennung unten. Gleiches Virenscan-Gating wie beim Anlegen |
| `GET` | `/documents/{id}/lock` | Aktuelle Sperre oder `null` |
| `POST` | `/documents/{id}/lock` | Sperre setzen (`locked_by`, `session_id`, optional `timeout_seconds`) — 409 bei Fremdsperre |
| `DELETE` | `/documents/{id}/lock` | Regulärer Unlock (`released_by`) — 403, wenn nicht der Halter |
| `POST` | `/documents/{id}/lock/force-release` | Administrativer Force-Unlock (`released_by`, optional `reason`) |
| `GET` | `/upload-config` | Format-Whitelist lesen (`allowed_content_types`, seit P5d-S1) |
| `PUT` | `/upload-config` | Format-Whitelist ändern - wirkt sofort auf den nächsten Upload |
| `GET`/`PUT` | `/audit-trace-config` | Basis-Protokollierungstiefe für den Forensik-Trace (`log_viewed`/`log_downloaded`, Default beide `true`, 5.4b, seit P7-S2c) — siehe "Audit-Tiefe" unten |
| `GET` | `/audit-trace-role-overrides` | Alle Rollen-Overrides der Audit-Tiefe (5.4b, seit P7-S2c) |
| `PUT`/`DELETE` | `/audit-trace-role-overrides/{role}` | Override für eine Rolle anlegen/ändern bzw. entfernen (5.4b, seit P7-S2c) — `404` bei `DELETE` einer unbekannten Rolle |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `document`: `id`, `title`, `folder_id`/`object_type_id` (opake Referenzen, s. u.), `attributes` (JSON, Custom-Felder gemäß Objekttyp), `current_version_number` (Zeiger auf die Hauptversion), `deleted_at`, `created_by/at/updated_at`, `derived_from_document_id`/`derived_from_version_number`/`originating_case_id` (seit P6-S3, Bearbeitungskopien, s. u.), `retention_until`/`full_deletion`/`pending_deletion_reason`/`deletion_reminder_sent_at`/`reminder_notify_email`/`force_delete_approval_requested_at` (5.2/5.2a, seit P7-S1, s. u.), `deleted_via_folder_id` (5.2, seit P7-S1b) — gesetzt, wenn dieses Dokument nicht einzeln, sondern kaskadiert über `POST /documents/cascade-trash` gelöscht wurde (weil sein Ordner in den Papierkorb verschoben wurde); `POST /documents/cascade-restore` stellt darüber gezielt nur die dadurch kaskadierten Dokumente wieder her, kein unabhängig einzeln gelöschtes.
- `document_version`: `document_id`, `version_number`, `storage_object_key`, `filename`, `content_type`, `size_bytes`, `checksum_sha256`, `is_conflict`, `based_on_version_number`, `comment`, `created_by/at`. Jede Zeile bleibt für immer abrufbar (2.1a).
- `document_lock`: genau eine aktive Zeile je gesperrtem Dokument (`document_id` als PK) — `locked_by`, `session_id`, `based_on_version_number`, `locked_at`, `expires_at`.
- `upload_config`: einzelne Zeile (`id=1`, seit P5d-S1) — `allowed_content_types` (JSON-Liste, leer = keine Einschränkung), `updated_at`.
- `legal_hold` (5.2, seit P7-S1): `id` (UUID PK), `document_id` (FK auf `document.id`), `reason` (nullable), `set_by`, `set_at`, `released_by` (nullable), `released_at` (nullable) — aktiv, solange `released_at IS NULL`.
- `deletion_register_entry` (5.2a, seit P7-S1): `id` (UUID PK), `document_id` (**bewusst kein FK** — die referenzierte `document`-Zeile ist zum Zeitpunkt des Eintrags bereits physisch entfernt), `trigger` (`"forced_deletion"`\|`"trash_expiry"`), `reason` (nullable), `triggered_by` (nullable), `occurred_at`.
- `retention_config` (5.2/5.2a, seit P7-S1): einzelne Zeile (`id=1`, gleiches Muster wie `UploadConfig`) — `deletion_reason_required` (Boolean), `reminder_lead_days` (Integer, nullable).
- `trash_config` (5.2, seit P7-S1): einzelne Zeile (`id=1`) — `restore_period_days` (Integer, Default 30).

`folder_id`/`object_type_id` sind opake Referenzen ohne FK-Erzwingung über Service-Grenzen hinweg, werden aber seit P3-S3 aktiv geprüft: `folder_id` (falls gesetzt) muss beim Folder Service existieren (sonst 400), `object_type_id` (falls gesetzt) validiert `attributes`+`title` gegen den Object-Type Service (sonst 400 mit Fehlerliste). Seit P4-S4 gilt dieselbe Validierung auch für `PATCH /documents/{id}` — `folder_id`/`object_type_id` selbst bleiben dabei bewusst unveränderlich (Verschieben/Retypisieren sind eigene Operationen mit anderen Konsistenzfragen, kein reines Metadaten-Update).

**Erzwungene Objekt-Hierarchie (2.2a, seit P5b-S1, ADR 0013)**: Die Existenzprüfung des Zielordners (`FolderClient.get()`, ersetzt das frühere reine `exists()`) liefert seither den vollen Ordner-Body inkl. dessen `object_type_id` — dieser wird zusammen mit `parent_is_root` (`true`, wenn `folder_id` fehlt oder `"root"` ist) an `POST /object-types/{id}/validate` übergeben, damit ein `allowedParentTypes` des Dokument-Objekttyps durchgesetzt werden kann. Nur relevant bei der Anlage (`POST /documents`) — Dokumente haben keine Verschiebe-Operation, `folder_id` bleibt nach Anlage unveränderlich (s. o.).

**Ad-hoc-Schema-Migration**: `attributes` kam erst in P3-S3 zur bestehenden `document`-Tabelle dazu. Ohne Alembic (siehe `CONTRIBUTING.md`) übernimmt ein `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in der Lifespan-Startup-Routine diese additive, defaultbehaftete Änderung idempotent — funktioniert nur für genau diese Art von Änderung (neue, nullable/defaultbehaftete Spalte), nicht für Umbenennungen/Typänderungen/Entfernen von Spalten. Sobald Schemaänderungen komplexer werden, wird echtes Alembic-Tooling nötig (siehe „Offene Entscheidungen" in `PROGRESS.md`).

## Kennzeichengenerator (2.2/4.4, seit P5e-S2)

Baut auf dem Object-Type Service auf (siehe `docs/services/object-type-service.md` "Kennzeichengenerator") — dieser Service kennt selbst kein Format, sondern fragt bei jeder Dokumentanlage mit gesetztem `object_type_id` `POST /object-types/{id}/next-kennzeichen` ab (`ObjectTypeClient.next_kennzeichen()`, `404` = kein Generator konfiguriert, dann bleibt `attributes["Kennzeichen"]` schlicht unbelegt).

- **`Kennzeichen` ist ein reservierter Attributschlüssel**: bei `POST /documents` wird ein vom Client mitgesendeter Wert für diesen Schlüssel immer verworfen (unabhängig davon, ob ein Generator konfiguriert ist) — die tatsächliche Zuweisung erfolgt ausschließlich serverseitig nach erfolgreicher Objekttyp-Validierung.
- **Erste echte Rollenprüfung im gesamten System**: `PATCH /documents/{id}` erkennt eine Änderung an `attributes["Kennzeichen"]` durch Vergleich von alt (`document.attributes`) gegen neu (`payload.attributes`, vollständiger Ersatz statt Merge, siehe unten) — weichen sie voneinander ab (auch das kommentarlose Weglassen des Schlüssels in einem sonst vollständigen Attribut-Replace zählt als Änderung, da es den Wert effektiv auf `null` setzt), muss der vom Gateway injizierte `X-DMS-Roles`-Header (kommagetrennt) die Rolle aus `Settings.kennzeichen_admin_role` (Default `"dms-admin"`) enthalten, sonst `403`. Bislang wertete **kein** Service im gesamten System diesen Header aus (siehe `PROGRESS.md` "Autorisierung") — die Rolle wird vom Auth Service idempotent im Realm angelegt (`ensure_realm_and_client`), die Zuweisung an konkrete Nutzer läuft vorerst außerhalb des Systems über die Keycloak Admin Console (keine eigene Rollenverwaltungs-API/-UI, siehe `docs/services/auth-service.md` "Offene Punkte").
- **Kein Format-Validitätscheck bei privilegierten manuellen Änderungen**: ein `dms-admin`-Principal kann einen beliebigen String setzen, auch einen, der nicht dem konfigurierten `kennzeichen_format` entspricht — bewusst nicht eingeschränkt, entspricht der reinen Rollenprüfung aus der Planung (siehe `PROGRESS.md`).
- **Wo `Kennzeichen` angezeigt wird** (vor dem Dateinamen, global oder je Objekttyp override) ist noch offen — folgt mit **P5e-S3**.

## Bearbeitungskopien (2.3, seit P6-S3)

Eine prozessspezifische Bearbeitungskopie (klassisches Beispiel: eine Schwärzung für die Akteneinsicht) ist laut Konzept **kein neuer Version des Ursprungsdokuments**, sondern ein eigenständiges, gesondert hinterlegtes Dokument mit Verweis auf das Ursprungsdokument (inkl. konkreter Ausgangsversion) und den auslösenden Vorgang/die auslösende Umlaufmappe — und soll "vollständig auditiert und ebenfalls versioniert wie jede andere Dokumentaktion" sein.

Diese Session hat dafür bewusst **keine neue Route** eingeführt, sondern `POST /documents` um drei optionale, nullable Formularfelder erweitert: `derived_from_document_id` (verweist auf `document.id`, echter FK innerhalb desselben Schemas), `derived_from_version_number` (Pflicht, sobald `derived_from_document_id` gesetzt ist — `400`, falls fehlend oder die referenzierte Version laut `repository.get_version()` nicht existiert), `originating_case_id` (opake Referenz auf eine Umlaufmappe im neuen `case-service`, keine Existenzprüfung — analog zu `folder_id`/`object_type_id`). Eine so angelegte Bearbeitungskopie ist danach ein **komplett eigenständiges Dokument**: eigene `id`, eigene Versionshistorie, durchläuft denselben Virenscan/Storage-Upload/Kennzeichen-Mechanismus wie jedes andere Dokument — die drei Felder sind reine Herkunftsmetadaten ohne Sonderbehandlung an anderer Stelle im Service.

`case-service` (P6-S3) ist der vorgesehene Aufrufer (Schwärzung eines in einer Umlaufmappe referenzierten Dokuments), ruft diesen Endpunkt aber selbst nicht auf — die Erstellung einer Bearbeitungskopie ist ein manueller/prozessgesteuerter Vorgang außerhalb dieser Session, das Datenmodell/die API-Erweiterung ist die dafür nötige Grundlage.

## Speicherung der Inhalte (3.6-Anbindung)

Objektschlüssel sind **inhaltsadressiert**: `documents/{document_id}/{sha256}`. Das vermeidet die Henne-Ei-Reihenfolge "Upload braucht die noch nicht vergebene Versionsnummer" und dedupliziert identische Inhalte innerhalb desselben Dokuments automatisch (z. B. wiederholtes Hochladen derselben Datei). Document Service spricht dafür ausschließlich `PUT`/`GET /objects/{key}` des Storage Service über HTTP an — kein Zugriff auf dessen Interna oder direkte Backend-Nutzung.

## Content-Type-Erkennung & Format-Whitelist (3.1/3.6, seit P5d-S1)

Der bei jedem Upload gespeicherte `content_type` wird **serverseitig aus den
tatsächlichen Magic-Bytes** ermittelt (`content_type_sniffer.py`, `python-magic`/
`libmagic`) - nicht mehr aus dem ungeprüft vom Browser gesendeten
`file.content_type`-Header übernommen. Behebt, dass z. B. `.txt`/`.json` je
nach Browser/Betriebssystem mit generischem oder schlicht falschem
Content-Type ankommen (Nutzer-Feedback nach Phase 5c, siehe `PROGRESS.md`).
Das Sniffing läuft vor dem Virenscan (kein unnötiger Scan-Aufruf bei
ohnehin abgelehntem Format) und gilt für Anlegen *und* Check-in gleichermaßen.

Eine admin-editierbare Whitelist (`UploadConfig`, gleiches
Einzelzeilen-Muster wie `OcrConfig`/`GuardConfig` der anderen Services)
erlaubt, den erkannten Content-Type gegen eine feste Liste zu prüfen - ein
nicht gelistetes Format wird mit `400` abgelehnt, bevor Inhalt/Metadaten
geschrieben werden. Leere Liste (Default) = keine Einschränkung, identisches
Verhalten zu vor P5d-S1.

## Virenscan vor Freigabe (10.3, seit P5-S1)

Jeder Upload (Anlegen *und* Check-in) wird **synchron und vor jedem Schreiben** gegen den Virus-Scan Service geprüft (`VirusScanClient.scan(...)`) — nicht asynchron über ein Event, siehe [ADR 0010](../adr/0010-virus-scan-synchronous-gating.md) für die Begründung. Fällt der Scan negativ aus, wird die Anfrage mit `422` (`{"error": "virus_detected", "threat_name": ...}`) abgelehnt, bevor Inhalt im Storage Service oder Metadaten in der eigenen DB landen. Ist der Virus-Scan Service nicht erreichbar, wird der Upload ebenfalls abgelehnt (`503`, fail-closed statt stillschweigend durchzulassen). Details zur Engine (austauschbar, Standard erkennt nur die EICAR-Testsignatur) siehe `docs/services/virus-scan-service.md`.

## Bearbeitungssperre & Konfliktbehandlung (4.2)

- Eine Sperre ist an `locked_by` + `session_id` gebunden und läuft nach einem konfigurierbaren Timeout automatisch ab (`default_lock_timeout_seconds`, keine Hintergrundprüfung nötig — Ablauf wird beim nächsten Zugriff bewertet, analog zum Registry-Service-Muster).
- **Force-Unlock löscht die Sperre vollständig** statt sie in einen dritten "überwacht"-Zustand zu versetzen (Abweichung von der wörtlichen Konzeptbeschreibung, siehe **ADR 0002** für die Begründung). Der Schutz vor stillem Datenverlust entsteht stattdessen durch eine **immer aktive optimistische Konflikterkennung** beim Check-in:
  - Jeder Check-in gibt `expected_base_version_number` an.
  - Stimmt dieser Wert mit der aktuellen Hauptversion überein → regulärer Check-in, neue Hauptversion.
  - Weicht er ab (z. B. weil in der Zwischenzeit jemand anderes nach einem Force-Unlock eingecheckt hat) → **Konfliktkopie**: eigenständige, weiterhin abrufbare Version (`is_conflict=true`, Dateiname `<name>_conflict_<user>_<zeitstempel>`), der Hauptversions-Zeiger bewegt sich nicht.
- Ein eigener Check-in beendet immer die eigene Sperre (auch im Konfliktfall — die Ausgangsbasis war ohnehin veraltet).
- **Vier-Augen-Prinzip (4.3) für Force-Unlock seit P6-S4 optional verdrahtet**: `POST /documents/{id}/lock/force-release` fragt vorher `GET /approval-config/document.force_unlock` beim Permission Service ab (`approval_client.py`). Ist Genehmigung aktiviert, wird die Sperre **nicht** sofort aufgehoben, sondern ein Freigabe-Request angelegt (Antwort `{status: "pending_approval", approval_request_id}`, Lock bleibt bestehen) — die tatsächliche Ausführung erfolgt erst über den neuen `consumer.py` (erster NATS-Konsument dieses Service überhaupt), sobald `permission.approval.approved` eintrifft. Ohne Konfiguration (Default) bleibt das Verhalten unverändert: sofortige Ausführung, Antwort `{status: "released", lock}`. Details/Architekturentscheidung siehe [ADR 0022](../adr/0022-four-eyes-approval-via-events.md) und `docs/services/permission-service.md` "Vier-Augen-Approval-Mechanismus".

## Aufbewahrung & Legal Hold (5.2, seit P7-S1)

Ein gemeinsames Feldpaar deckt sowohl die reguläre Aufbewahrungsfrist (5.2) als auch die Zwangslöschung (5.2a) ab: `Document.retention_until` (Datum) + `full_deletion` (Boolean). Beim Erreichen von `retention_until` ohne aktiven Legal Hold entscheidet `full_deletion`: `false` → regulärer Papierkorb (bestehende `deleted_at`-Logik), `true` → direkt physische Löschung (siehe "Zwangslöschung & Löschregister" unten).

- **Kein neuer BPMN-/Timer-Dienst**: `workflow-service` hat keinen generischen "Callback nach N Tagen"-Mechanismus, nur echte Prozessinstanzen mit Timer-/Boundary-Events. Stattdessen bekommt dieser Service einen eigenen `_retention_poll_loop` (`Settings.retention_poll_interval_seconds`, Default 3600s) — exakt dasselbe Idiom wie `workflow-service`s `_sla_poll_loop` ([ADR 0020](../adr/0020-sla-timer-polling.md)). Eine BPMN-Anbindung bleibt ein späterer, optionaler Ausbau; das Konzept selbst erlaubt die direkte, prozessunabhängige Konfiguration am Objekt/Objekttyp ausdrücklich.
- **Default-Frist je Objekttyp**: `default_retention_days` auf `ObjectType` (siehe `docs/services/object-type-service.md`) wird beim Anlegen eines Dokuments **einmalig** zu einem konkreten `created_at + default_retention_days`-Datum aufgelöst — kein wiederholtes Nachschlagen, falls sich der Typ-Default später ändert. Ein beim Anlegen/über `PUT .../retention` manuell gesetztes `retention_until` hat Vorrang.
- **Legal Hold überschreibt jede fällige Aktion** (weder Papierkorb noch physische Löschung noch Löscherinnerung), solange er aktiv ist (`legal_hold.released_at IS NULL`) — wörtliche Umsetzung von 5.2. Mehrere Holds je Dokument sind möglich; jeder muss einzeln aufgehoben werden, damit das Dokument wieder "frei" ist.
- **Löscherinnerung** (optional, `RetentionConfig.reminder_lead_days`): kein eigener Timer, sondern ein dritter Zweig desselben Poll-Loops — erkennt "innerhalb der Vorlaufzeit, Erinnerung noch nicht gesendet" (`Document.deletion_reminder_sent_at`) und publiziert `document.deletion.reminder` (konsumiert von `notification-service`, siehe dort).
- **Papierkorb mit Wiederherstellungsfrist**: `TrashConfig.restore_period_days` (Default 30) bestimmt, wie lange `POST /documents/{id}/restore` nach dem Soft-Delete noch möglich ist (`409` danach) — abgelaufene Einträge werden vom selben Poll-Loop physisch bereinigt (`trigger="trash_expiry"` im Löschregister, siehe unten), **ohne** automatischen Governance-Bypass (siehe dort).

## Zwangslöschung & Löschregister (5.2a, seit P7-S1)

Ergänzend zur regulären Aufbewahrungsfrist: das umgekehrte Szenario, bei dem ein Dokument nach Ablauf **verpflichtend physisch** gelöscht wird (`full_deletion=true`), inklusive optionalem Löschgrund und einem separaten, vom gelöschten Objekt unabhängigen Löschregister.

- **Löschgrund nur transient auf dem Dokument**: `Document.pending_deletion_reason` wird ausschließlich zwischen Terminierung und tatsächlicher Ausführung gehalten (Tage/Wochen Abstand möglich). Bei der eigentlichen physischen Löschung wandert der Grund in die persistente `deletion_register_entry`-Zeile **und** in ein `document.force_deleted`-Event (Audit-Trail) — die `Document`-Zeile selbst wird komplett entfernt. Der Grund "überlebt" das Objekt also nur in Register + Audit-Trail, nie als Property eines noch existierenden Objekts.
- **Pflicht/optional konfigurierbar**: `RetentionConfig.deletion_reason_required` (installationsweiter Default) + `ObjectType.deletion_reason_required_override` (Tri-State, überschreibt den globalen Default für einzelne Dokumentklassen) — `PUT /documents/{id}/retention` lehnt einen fehlenden Grund mit `422` ab, wenn er laut Auflösung Pflicht ist.
- **Vier-Augen-Prinzip (4.3, optional)**: wiederverwendet 1:1 den bestehenden Force-Unlock-Präzedenzfall (`approval_client.py`, `consumer.py`) über den neuen Aktionstyp `document.force_delete` — keine Änderung an `permission-service` nötig außer der Konfigurationszeile (`PUT /approval-config/document.force_delete`). Ist Genehmigung aktiviert, legt der Poll-Loop statt sofortiger Ausführung einen Freigabe-Request an (`Document.force_delete_approval_requested_at` verhindert doppelte Requests bei jedem weiteren Durchlauf); die tatsächliche Löschung erfolgt erst über einen neuen Consumer-Handler, sobald `permission.approval.approved` mit `action_type == "document.force_delete"` eintrifft.
- **Physische Löschung** (`retention_actions.execute_forced_deletion`): löscht jede `document_version` über `storage-service` mit `bypass_governance=True` — die sanktionierte Ausnahme vom Object-Lock-Schutz aus 5.1 (siehe `docs/services/storage-service.md` "Object-Lock/WORM", [ADR 0030](../adr/0030-storage-object-lock-governance-mode.md)) —, schreibt die `deletion_register_entry`, entfernt `Document`+`DocumentVersion`-Zeilen und publiziert `document.force_deleted`. Papierkorb-Ablauf (`trigger="trash_expiry"`) läuft dagegen **ohne** automatischen Bypass (`retention_actions.purge_expired_trash_entry`) — bei Blockade durch eine Governance-Sperre bleibt der Eintrag für einen späteren Durchlauf stehen, kein stiller Datenverlust durch einen unbeabsichtigten Bypass.
- **Löschregister nicht hash-verkettet** (anders als `audit-service`) und **nicht Backup-differenziert** — die vom Konzept verlangte "separat gesicherte, vom regulären Backup-Zyklus unabhängige" Aufbewahrung ist nicht vollständig erfüllbar, da es noch keine Backup/Restore-Phase gibt (Phase 11). Kompensiert dadurch, dass jede Zeile zusätzlich als reguläres Event publiziert wird, das `audit-service`s bestehende hash-verkettete Kette mitschreibt — dieselbe Tamper-Evidence wie überall sonst, nur (noch) keine separate Backup-Politik. Bewusst eine eigene, sofort abfragbare `GET /deletion-register`-API in diesem Service statt einer Abhängigkeit von einer künftigen `audit-service`-Filter-API.
- **Legal-Hold-Rollenprüfung**: keine neue RBAC-Differenzierung in dieser Session — wer einen Legal Hold setzen/aufheben darf, ist (wie die meisten Aktionen ohne Backend-Rollenprüfung außer den bereits bestehenden Ausnahmen) offen, siehe "Offene Punkte".

## Löschantrag-Workflow für reguläre Nutzer (5.2, seit P7-S1c)

Drittes, unabhängiges Aufbewahrungs-Szenario neben der regulären Frist und der Zwangslöschung: normale Nutzer sollen eine reguläre Papierkorb-Löschung nur noch **beantragen** können, statt sie direkt auszuführen — inkl. der Vorgabe, dass auch eine privilegierte Person nicht alleine löschen darf.

- **Eigener Aktionstyp `document.delete`**, getrennt von `document.force_delete` — Letzteres bleibt für die retentionsgetriggerte, poll-loop-ausgelöste Zwangslöschung, `document.delete` gatet stattdessen die manuelle, nutzerausgelöste `POST /documents/{id}/trash`. Beide unabhängig voneinander per `PUT /approval-config/{action_type}` konfigurierbar.
- **Gate-Prüfung direkt im Endpunkt**, exaktes Muster wie `force_release_lock` (4.2, P6-S4): `TrashResult{status: "trashed"|"pending_approval", document, approval_request_id}`. Serverseitig durchgesetzt, unabhängig davon, über welchen UI-Weg der Aufruf ausgelöst wurde.
- **Keine neue Selbstgenehmigungs-Logik nötig** — `permission-service` verhindert bereits generisch, dass Initiator und Genehmiger identisch sind (`NotInitiatorAllowedError`). Erfüllt "auch ein Löschadmin darf nicht alleine löschen" ohne jedes neue Rollenkonzept.
- **Consumer-Handler** (`consumer.py`, `_handle_delete_approved`) führt nach `permission.approval.approved` mit `action_type == "document.delete"` die zuvor zurückgestellte `repository.delete_document` aus — Copy-Paste-Muster des bestehenden `document.force_unlock`-Zweigs.
- **Genehmigungs-Inbox**: neue, minimale `ApprovalsPane` in der User-UI (nicht Admin-UI, da sich reguläre Nutzer hier gegenseitig genehmigen) — siehe `docs/services/user-ui.md`. Bewusst nur für `document.delete`/`folder.delete` gefiltert, keine generische Alle-Aktionstypen-Inbox (das ist als späterer "Administrativer Papierkorb" vorgemerkt, siehe `PROGRESS.md`).

## Audit-Tiefe für den Forensik-Trace (5.4b, seit P7-S2c)

Grundlage für die objektbezogene Nachverfolgung (Forensik-Trace, siehe `docs/services/reporting-service.md`) ist u. a. lückenlose Sichtbarkeit lesender Zugriffe — bis P7-S2c publizierte dieser Service dafür kein einziges Event (`GET /documents/{id}` und die Content-Downloads liefen komplett unauditiert). Seit dieser Session konfigurierbar protokollierbar:

- **Zwei neue Event-Typen**: `document.viewed` (nur beim Einzelabruf `GET /documents/{id}`, nicht beim Listing) und `document.downloaded` (beide Content-Endpunkte) — beide fallen unter das bereits bestehende `document.>`-Wildcard, `audit-service` brauchte dafür keine Code-Änderung.
- **Basis-Konfiguration + Rollen-Overrides**: `AuditTraceConfig` (Singleton, `log_viewed`/`log_downloaded`, **Default beide `true`** — maximale Nachvollziehbarkeit als Werkseinstellung) legt fest, ob überhaupt protokolliert wird. `AuditTraceRoleOverride` (PK `role`, freier Text ohne Existenzprüfung gegen `permission-service`/Keycloak — gleiche bestehende Lücke wie bei jedem anderen Rollennamen in diesem System) erlaubt pro Rolle einen Override je Kategorie (`null` = Basis gilt).
- **Auflösung** (`repository.resolve_should_log`, aufgerufen über `main._should_log_document_access`): die Rollen des Aufrufers kommen aus dem bereits vom Gateway injizierten `X-DMS-Roles`-Header (gleiches Muster wie `_has_kennzeichen_admin_role` beim Kennzeichengenerator). **Konfliktregel bei mehreren zugewiesenen Rollen mit widersprüchlichen Overrides**: hat irgendeine Rolle einen expliziten `true`-Override, wird protokolliert ("protokollieren" gewinnt) — nur wenn *alle* mit einem Override versehenen Rollen `false` sagen, wird nicht protokolliert, sonst gilt die Basis. Sicherheits-first: eine Rolle, die mehr Protokollierung verlangt, kann nicht durch eine andere Rolle desselben Nutzers stillschweigend unterlaufen werden.
- **Der Akteur** (`actor` im publizierten Event) kommt aus dem gateway-injizierten `X-DMS-Username`-Header — fehlt er (z. B. direkter, nicht über das Gateway laufender Aufruf), bleibt `actor` `null`, das Event wird aber trotzdem publiziert (Basis-Default bleibt maßgeblich).
- **Lokal statt zentral konfiguriert**, bewusst wie jede andere Config in diesem System (`UploadConfig`, `RetentionConfig`, ...) — vermeidet einen synchronen Cross-Service-Konfig-Abruf auf dem heißen Lesepfad (jeder Dokumenten-Klick/Download).
- **Admin-UI**: `/audit-trace-settings/` (`AuditTraceSettings`, siehe `docs/services/admin-ui.md`).

## Events

**Publiziert** (Stream `document`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `document.created` | `{title, created_by, folder_id}` (seit P7-S2b: `folder_id` zusätzlich im Payload, Grundlage für die Ordner-Gruppierung des `reporting-service`-Dokumentenaufkommen-Berichts, siehe `docs/services/reporting-service.md`), zusätzlich `{derived_from_document_id}` bei einer Bearbeitungskopie (seit P6-S3, s. u.) |
| `document.version.created` | `{version_number, is_conflict, created_by}` |
| `document.lock.force_released` | `{original_locked_by, released_by, reason}` |
| `document.metadata.updated` | `{title}` (seit P4-S4) |
| `document.deleted` | `{deleted_by}` |
| `document.deletion.reminder` | `{retention_until, full_deletion}` (5.2a, seit P7-S1, konsumiert von `notification-service`) |
| `document.force_deleted` | `{trigger: "forced_deletion", reason, triggered_by}` (5.2a, seit P7-S1) |
| `document.trash_purged` | `{trigger: "trash_expiry"}` (5.2a, seit P7-S1) |
| `document.viewed` | `{}` (Forensik-Trace, 5.4b, seit P7-S2c) — nur bei `GET /documents/{id}` (Einzelabruf), **nicht** bei der Listing-Route `GET /documents?folder_id=`, um nicht pro Ordnerinhalt Dutzende Events zu erzeugen. Abhängig von der Audit-Tiefe-Konfiguration, siehe unten |
| `document.downloaded` | `{version_number}` (Forensik-Trace, 5.4b, seit P7-S2c) — beide Content-Endpunkte. Abhängig von der Audit-Tiefe-Konfiguration, siehe unten |

**Konsumiert** (seit P6-S4, erster Konsument dieses Service überhaupt): `permission.approval.approved` — relevant für `action_type == "document.force_unlock"` (Force-Unlock, seit P6-S4) und seit P7-S1 zusätzlich `action_type == "document.force_delete"` (führt die zuvor aufgeschobene Zwangslöschung aus, siehe "Zwangslöschung & Löschregister" oben); alle anderen Aktionstypen werden ignoriert.

**Audit-Anbindung**: Audit Service konsumiert seit dieser Session zusätzlich `document.>` (vorher nur `registry.>`) — 4.2 verlangt explizit vollständige Auditierung von Force-Unlock/Konfliktkopie. Force-Unlock und die daraus ggf. entstehende Konfliktkopie erzeugen zwei separate, aber im Audit-Trail über `subject=document_id` verknüpfbare Ereignisse.

**Rendering-Anbindung (seit P5-S2)**: Rendering Service konsumiert `document.created`/`document.version.created`, um automatisch Ersatzdarstellungen/Vorschauen zu erzeugen (siehe `docs/services/rendering-service.md`) — reine Konsumentenbeziehung, dieser Service selbst weiß nichts vom Rendering Service.

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- **Kennzeichen-Anzeige im Frontend** (vor dem Dateinamen, global oder je Objekttyp überschreibbar) noch nicht angebunden — folgt mit P5e-S3.
- **`document.viewed`/`document.downloaded` decken nur Dokumente ab** (5.4b, seit P7-S2c) — Ordner-Lesezugriffe (`folder-service`) bleiben weiterhin unauditiert, war nicht Teil des Konzepttexts ("gelesene ... Dokumente"). Ebenso keine Existenzprüfung der Rollennamen in `AuditTraceRoleOverride` gegen `permission-service`/Keycloak — gleiche bestehende Lücke wie bei jedem anderen Rollennamen im System.
- **Keine Rollenzuweisungs-API/-UI**: `dms-admin` muss aktuell direkt über die Keycloak Admin Console zugewiesen werden (siehe oben, "Kennzeichengenerator").
- Vier-Augen-Prinzip für Force-Unlock seit P6-S4 optional verfügbar (siehe oben) — Default bleibt ungated, ebenso kein Rückkanal, der `permission-service` einen fehlgeschlagenen (z. B. inzwischen anderweitig aufgelösten) Vollzug meldet.
- **Ordner haben seit P7-S1b ihr eigenes, paralleles Aufbewahrungs-/Legal-Hold-/Zwangslöschungs-Muster** (`folder-service`, eigene Tabellen statt Wiederverwendung dieser hier) — dieser Service ist über die neuen `cascade-trash`/`cascade-restore`/`count-active`-Endpunkte (s. o.) synchron eingebunden, wenn ein Ordner in den Papierkorb verschoben/wiederhergestellt bzw. auf Nicht-Leerheit vor Zwangslöschung geprüft wird. Siehe `docs/services/folder-service.md`.
- **Keine Legal-Hold-Rollenprüfung** (5.2, seit P7-S1) — wer einen Hold setzen/aufheben darf, ist nicht eingeschränkt, siehe oben.
- **Löschregister nicht Backup-differenziert** (5.2a) — kompensiert nur über die Audit-Service-Hash-Kette, siehe oben; echte Backup-Trennung erst mit Phase 11.
- **Löschregister/Legal-Hold-Tabellen leben in diesem Service statt einem eigenen Compliance-Service** — bewusst, um eine verfrühte Auslagerung zu vermeiden, bevor der Bedarf über mehr als einen Objekttyp hinweg feststeht (neu zu bewerten, sobald z. B. Umlaufmappen ebenfalls Aufbewahrung brauchen, Phase 15).
- **Kein Admin-UI-Toggle für `document.delete`/`document.force_delete`/`document.force_unlock`** (5.2, seit P7-S1c) — identische, bereits bestehende Lücke, nur über die rohe `PUT /approval-config/{action_type}`-API konfigurierbar. Eine generische "Vier-Augen-Einstellungen"-Seite für alle Aktionstypen wäre ein sinnvoller, aber separater künftiger Schnitt.
- **Genehmigungs-Inbox nur für `document.delete`/`folder.delete` gefiltert** (5.2, seit P7-S1c) — kein genereller Ersatz für ein vollständiges Löschregister-/Wiederherstellungs-Cockpit; laut Nutzerhinweis Vorstufe zu einem später geplanten "Administrativen Papierkorb" (vermutlich Phase 15 "Sonderbereiche").
- **Umlaufmappen-Referenzen (2.3, seit P6-S3)**: der neue `case-service` greift ausschließlich lesend über `GET /documents/{id}` auf `current_version_number`/`deleted_at` zu — hier musste dafür nichts geändert werden. Bearbeitungskopien (ebenfalls 2.3) sind dagegen diese Session (siehe oben) — kein neuer Endpunkt, drei optionale Herkunftsfelder an `POST /documents`. Ersatzdarstellungen (2.4) sind seit P5-S2 umgesetzt (siehe `docs/services/rendering-service.md`), ohne dass dieser Service dafür geändert werden musste — der Rendering Service konsumiert die bereits vorhandenen `document.>`-Events und ruft die bereits vorhandenen Versions-/Content-Endpunkte auf.
- **Keine Existenzprüfung für `originating_case_id`**: opake Referenz auf eine Umlaufmappe im `case-service`, analog zu `folder_id`/`object_type_id` — ein unbekannter Wert wird nicht abgelehnt.
- Virenscan-Gating erhöht die Upload-Latenz um die Scan-Zeit und scannt auch dann, wenn ein Check-in wegen veralteter `expected_base_version_number`/Lock-Konflikt ohnehin abgelehnt würde (unnötige, aber nicht falsche Arbeit) — siehe ADR 0010 "Konsequenzen".
- Kein Rückwirkungs-Check und keine Zyklen-Erkennung für `allowedParentTypes` (siehe ADR 0013) — dieselbe Einschränkung wie beim Object-Type/Folder Service.
