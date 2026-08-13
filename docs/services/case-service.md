# case-service

**Verantwortung:** Umlaufmappen (Konzept 2.3) — bündeln Referenzen (keine eigenen Kopien) auf Dokumente, die zu einem Vorgang gehören. Eigener Lebenszyklus über eine Prozessinstanz in [workflow-service](../adr/0018-spiffworkflow-lgpl-license.md) (7.1, P6-S1): während die Umlaufmappe offen ist, wird je referenziertem Dokument dynamisch die aktuellste Version aufgelöst; beim Erreichen des BPMN-Endzustands wird die Referenzstruktur als **Abschluss-Snapshot** fixiert. Kein UI. Seit Post-Roadmap Phase 19 Session 5 (ADR 0070) über `case.read`/`case.write` RBAC-gegated (siehe "Offene Punkte").

**Konzept-Referenz:** 2.3, 5.6 (Aussonderung, seit P7-S3b)
**Eigenes Postgres-Schema:** `case` (Tabellen `cases`, `case_document_reference`, `case_archival_config`) — `"case"` ist ein reserviertes SQL-Schlüsselwort (`CASE WHEN`); SQLAlchemy quotet es in generierten DDL-Statements automatisch, rohe SQL-Strings (`main.py`, `tests/conftest.py`) müssen es selbst als `"case"` quoten. Die Tabelle für Umlaufmappen heißt deshalb bewusst `cases` (Plural), nicht `case`, um diese Quoting-Pflicht nicht doppelt zu brauchen.

## API

**RBAC seit Post-Roadmap Phase 19 Session 5** ([ADR 0070](../adr/0070-case-service-rbac.md)): jeder Endpunkt unten verlangt `X-DMS-Principal` (`401` ohne) und prüft `case.read`/`case.write` gegen `permission-service` (`403` bei Ablehnung, `resource_id="root"`) — **außer** `GET /cases/due-for-archival` und `PUT /cases/{id}/archived`, beides rein interne Rückrufe von `archival-service` ohne menschlichen Aufrufer, bewusst ungegatet.

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/cases` | Anlegen (`name`, optional `object_type_id`/`attributes`, `process_definition_id`, `created_by`, optional `initial_data`) — validiert `object_type_id` (falls gesetzt) gegen den Object-Type Service (immer als Wurzel-Objekt, keine Ordner-Elternschaft), startet danach eine Prozessinstanz in workflow-service mit `business_key = case_id`. `400` bei unbekanntem `process_definition_id`. Vergibt seit **P15-S3** zusätzlich automatisch eine `vorgangsnummer` (2.3/2.5), siehe unten |
| `GET` | `/cases` | Liste, Filter `status`/`object_type_id` |
| `GET` | `/cases/by-vorgangsnummer?value=...` | Vorgangsnummer-Suche (2.5/3.3, seit P15-S3) — vor `/cases/{id}` registriert. Liefert eine Liste (Konsistenz mit `document-service`s Kennzeichen-Lookup), obwohl `vorgangsnummer` per Konstruktion global eindeutig ist. Für den neuen `mail-connector` |
| `GET` | `/cases/due-for-archival` | Interner Aufruf von `archival-service` (5.6, seit P7-S3b) — vor `/cases/{id}` registriert, damit `"due-for-archival"` nicht als `{case_id}` interpretiert wird |
| `GET` | `/cases/{id}` | Detail — `404` |
| `POST` | `/cases/{id}/documents` | Dokumentreferenz hinzufügen (`document_id`, `added_by`) — `400` falls `document_id` laut Document Service unbekannt, `404` falls die Umlaufmappe unbekannt ist, `409` falls sie bereits abgeschlossen ist |
| `DELETE` | `/cases/{id}/documents/{document_id}` | Referenz weich entfernen (`removed_by`) — Zeile bleibt erhalten (Nachvollziehbarkeit), `404`/`409` analog |
| `GET` | `/cases/{id}/documents` | Alle Referenzen (aktiv + entfernt) inkl. aufgelöster Version: offen → live `current_version_number`/`deleted_at` aus dem Document Service, geschlossen → fixierter `snapshot_version_number`, kein Document-Service-Aufruf mehr nötig |
| `POST` | `/cases/{id}/archive-request` | Manueller Aussonderungs-Trigger (5.6, seit P7-S3b) — `409`, falls die Umlaufmappe noch nicht abgeschlossen ist |
| `GET` | `/cases/{id}/archive-status` | Aussonderungsstatus lesen (`archive_after`/`archived_at`, seit P7-S3b) |
| `PUT` | `/cases/{id}/archived` | Interner Rückruf von `archival-service`, sobald das XDOMEA-Paket verifiziert ist (seit P7-S3b) — publiziert `case.archived` |
| `GET`/`PUT` | `/case-archival-config` | Installationsweite Aussonderungs-Konfiguration (`default_archive_after_days_closed`, `archive_encryption_enabled`, seit P7-S3b) |
| `GET`/`PUT` | `/case-number-config` | Format-String der Vorgangsnummer (2.5, seit P15-S3, Default `{YYYY}-{Laufende_Nummer}`) — `400` bei unbekanntem Platzhalter oder fehlendem `{Laufende_Nummer}` |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `cases`: `id` (UUID), `name`, `object_type_id` (opake Referenz, optional), `attributes` (JSON), `status` (`"open"`\|`"closed"`), `process_definition_id`/`process_instance_id` (opake Referenzen auf workflow-service), `created_by`/`created_at`, `closed_at` (nullable), `archive_after`/`archived_at` (beide nullable, 5.6, seit P7-S3b), `vorgangsnummer` (nullable — nur für ab P15-S3 neu angelegte Fälle vergeben, 2.3/2.5).
- `case_document_reference`: `id`, `case_id` (FK), `document_id` (opake Referenz auf document-service), `added_by`/`added_at`, `removed_by`/`removed_at` (beide nullable — weiche Löschung statt Hard-Delete), `snapshot_version_number` (nullable, nur nach Abschluss gesetzt).
- `case_archival_config` (5.6, seit P7-S3b): einzelne Zeile (`id=1`, gleiches Singleton-Muster wie document-services `RetentionConfig`) — `default_archive_after_days_closed` (Integer, nullable), `archive_encryption_enabled` (Boolean), `updated_at`.
- `case_number_config`/`case_sequence` (2.5, seit P15-S3): siehe "Vorgangsnummer" unten.

`business_key` der gestarteten Prozessinstanz ist bewusst **identisch mit der Case-ID** (kein separates Feld) — einzige Grundlage dafür, wie `consumer.py` den späteren Abschluss einer Instanz der richtigen Umlaufmappe zuordnet (siehe "Abschluss-Snapshot" unten).

## Zweistufiges Referenzmodell (2.3)

- **Während die Umlaufmappe offen ist**: `GET /cases/{id}/documents` löst für jede aktive Referenz `current_version_number`/`deleted_at` live über `GET /documents/{id}` (document-service) auf — kein eigener Zustand, immer der aktuelle Stand. Ein weich gelöschtes Original bleibt über diesen Endpunkt weiterhin abrufbar (`deleted_at` gesetzt statt `404`) — genau das deckt die Konzept-Anforderung "Referenz bleibt bei Löschung des Originals nachvollziehbar bestehen" bereits ab, ohne eigene Logik in diesem Service.
- **Abschluss-Snapshot**: sobald die zugehörige Prozessinstanz den BPMN-Endzustand erreicht (`workflow.instance.completed`, siehe unten), wird für jede zu diesem Zeitpunkt noch aktive Referenz die dann aktuelle Version in `snapshot_version_number` fixiert. Spätere Änderungen am referenzierten Originaldokument wirken sich danach nicht mehr auf diese Umlaufmappe aus. Bereits entfernte Referenzen (weich gelöscht vor Abschluss) bleiben ohne Snapshot.
- Ist ein referenziertes Dokument beim Abschluss über document-service nicht mehr erreichbar, bleibt die Referenz ohne `snapshot_version_number` bestehen (kein Fehler, keine verlorene Referenz) — dieselbe "nachvollziehbar bestehen bleiben"-Behandlung wie beim regulären Lesezugriff.

## Abschluss-Snapshot: Event statt Polling

case-service ist der **erste Konsument** von workflow-services `workflow.instance.completed` (bisher reiner Producer, siehe `docs/services/workflow-service.md`). Der Handler (`consumer.py`) sucht die Umlaufmappe direkt über `business_key` — findet er keine passende (Instanz stammt nicht von case-service) oder ist sie bereits `closed`, wird das Event ignoriert. Es gibt keinen Polling-Endpoint in workflow-service; dieses Event ist der einzige verfügbare Mechanismus.

**Bekannte Race-Bedingung** (nicht behoben, siehe "Offene Punkte"): startet `POST /cases` einen vollständig automatisierten Prozess ohne Manual Task, kann workflow-service `workflow.instance.completed` bereits publizieren, bevor die eigene `Case`-Zeile in derselben Anfrage committet ist — das Event würde dann keine passende Umlaufmappe finden und verpufft, die Umlaufmappe bliebe fälschlich `open`. Für die Live-Verifikation dieser Session wurde bewusst ein Prozess mit Manual Task verwendet (erreicht den Endzustand erst nach explizitem Task-Abschluss, deutlich nach dem `POST /cases`-Response), sodass dieser Fall nicht auftritt.

## Objekttyp-Integration (2.2)

Wie document-service/folder-service: `object_type_id` ist optional, bei gesetztem Wert wird `POST /object-types/{id}/validate` aufgerufen. Anders als bei Dokumenten/Ordnern wird dabei **immer ein Wurzel-Objekt** angenommen (`parent_object_type_id=None`, `parent_is_root=True`) — eine Umlaufmappe hängt konzeptionell nicht im Ordnerbaum (2.2a), sondern ist ein eigenständiger Objekttyp.

## Aussonderung (5.6, seit P7-S3b)

Nur **geschlossene** Umlaufmappen sind aussonderungsfähig — die eigentliche Transfer-Mechanik (XDOMEA-4.0.0-Nachricht erzeugen, referenzierte Dokumentinhalte paketieren, verschlüsseln, archivieren) liegt vollständig in `archival-service` (siehe `docs/services/archival-service.md` "XDOMEA-Aussonderung für Umlaufmappen"), dieser Service bleibt alleinige Autorität für die Case-Lebenszyklusfelder.

- **Auslösung**: anders als bei Dokumenten (`ObjectType.default_archive_after_days`, per-Objekttyp) gibt es hier **keinen per-Objekttyp-Default** — `ObjectType.applies_to` ist strikt `"document"`\|`"folder"`, Umlaufmappen nutzen `object_type_id` nur lose zur Attribut-Validierung, keine eigene `applies_to`-Kategorie. Stattdessen ein **installationsweiter** `CaseArchivalConfig.default_archive_after_days_closed` (Singleton, gleiches Muster wie `RetentionConfig`), aufgelöst in `close_case()` zum Abschlusszeitpunkt (`archive_after = closed_at + default_archive_after_days_closed`) — nicht bei Anlage wie bei Dokumenten, da vorher keine Aussonderung möglich ist. Zusätzlich manueller Trigger `POST /cases/{id}/archive-request`, der `409` liefert, falls die Umlaufmappe noch offen ist.
- **Verschlüsselung**: ebenfalls installationsweit (`CaseArchivalConfig.archive_encryption_enabled`), aus demselben Grund kein Pendant zu `ObjectType.archive_encryption_enabled`.
- **Kein Dehydrieren für Cases selbst** — eine Umlaufmappe besitzt keinen eigenen Live-Inhalt (nur Referenzen), nur die referenzierten Dokumente durchlaufen ihren eigenen, unabhängigen P7-S3-Archivierungs-/Dehydrierungs-Zyklus.
- **`GET /cases/due-for-archival`** filtert auf `status="closed" AND archive_after <= now AND archived_at IS NULL` — vor `/cases/{id}` registriert (Route-Reihenfolge, s. o.).

## Vorgangsnummer (2.3/2.5, seit P15-S3)

Jede neue Umlaufmappe bekommt automatisch eine server-generierte, **installationsweit eindeutige** `vorgangsnummer` (`POST /cases` ruft intern `repository.next_vorgangsnummer()` auf, bevor die Zeile angelegt wird) — Grundlage für den neuen `mail-connector` (2.5/3.3), der eingehende Post anhand einer im Betreff/Text gefundenen Vorgangsnummer automatisch einer Umlaufmappe zuordnen können muss.

- **Ein einzelner installationsweiter Zähler statt eines je-Objekttyp-Zählers** (anders als document-services Kennzeichengenerator, P5e-S1) — `ObjectType.applies_to` kennt `"case"` nicht als eigene Kategorie (siehe "Objekttyp-Integration" oben), ein eigener, einfacherer Generator direkt in diesem Service vermeidet eine invasive Erweiterung von `object-type-service`. Format konfigurierbar über `GET`/`PUT /case-number-config` (Default `{YYYY}-{Laufende_Nummer}`, Platzhalter `{YYYY}`/`{YY}`/`{Laufende_Nummer}`), atomarer Jahres-Zähler (`case_sequence`, `SELECT ... FOR UPDATE`, identisches Idiom wie `object_type_service.ObjectTypeSequence`).
- **Nicht über PATCH änderbar** (anders als `Kennzeichen`) — ein stabiler, rein systemseitig vergebener Bezug ist Voraussetzung für verlässliches Matching, kein Anwendungsfall für eine nachträgliche Admin-Änderung in dieser Session.
- **`GET /cases/by-vorgangsnummer?value=...`** liefert eine Liste (Konsistenz mit dem analogen Dokument-Endpunkt), obwohl die Vorgangsnummer per Konstruktion global eindeutig ist.
- Vollständige Architekturbegründung: [ADR 0053](../adr/0053-posteingang-postausgang-pop3-loopback-connector-and-cross-service-matching.md).

## Events

**Publiziert** (Stream `case`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `case.created` | `{name, created_by}` |
| `case.document.added` | `{document_id, added_by}` |
| `case.document.removed` | `{document_id, removed_by}` |
| `case.closed` | `{process_instance_id}` |
| `case.archived` | `{}` (5.6, seit P7-S3b) — Rückruf von `archival-service`, sobald das XDOMEA-Paket verifiziert ist |

**Konsumiert:** `workflow.instance.completed` (siehe "Abschluss-Snapshot" oben).

**Audit-Anbindung**: Audit Service konsumiert seit dieser Session zusätzlich `case.>` (gleiches Sofort-Ergänzungs-Muster wie bei jedem vorherigen neuen Producer-Stream).

## Selbst-Registrierung (Konzept 3.2a)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`), identisches Muster wie jeder andere Service. Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`. Das Gateway benötigt keine eigene Codeänderung — Routing läuft vollständig dynamisch über `service_type="case-service"`.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

`uv run pytest services/case-service/tests` (**45 Tests**, davon 6 neu seit **P15-S3**: eindeutige `vorgangsnummer` je neuer Umlaufmappe, `GET /cases/by-vorgangsnummer` Treffer/leer, `case-number-config`-Roundtrip inkl. Ablehnung eines Formats ohne `{Laufende_Nummer}`/mit unbekanntem Platzhalter; davor 39 Tests, davon 13 neu seit P7-S3b):
- `test_repository.py` — reine DB-Logik (Anlegen, Referenzen hinzufügen/entfernen, Abschluss-Snapshot inkl. Randfälle: entfernte Referenz bleibt ohne Snapshot, fehlende `document_id` im Snapshot-Dict bleibt ohne Snapshot) — läuft gegen echte Postgres wie überall im Projekt, keine HTTP-Aufrufe (`repository.py` kennt keine Sibling-Services). Seit P7-S3b zusätzlich: `archive_after`-Auflösung bei `close_case` (mit/ohne konfigurierten Default), `CaseArchivalConfig`-CRUD, `list_due_for_archival`-Filter, `request_archive` inkl. `CaseNotClosedError` bei offener Umlaufmappe, `mark_archived`.
- `test_consumer.py` — simuliertes `workflow.instance.completed`-Event direkt an den Handler (kein echtes NATS nötig, gleiches Muster wie `notification-service/tests/test_consumer.py`), Fake-`DocumentClient` statt echtem HTTP.
- `test_api.py` — echte Integrationstests gegen lokal erreichbare `workflow-service`-/`document-service`-/`object-type-service`-Instanzen (gleiches Muster wie document-services `folder_client`/`object_type_client`-Tests) — jeder Testfall legt sich seine eigene Prozessdefinition/sein eigenes Testdokument an. Deckt bewusst **nicht** die tatsächliche asynchrone Event-Zustellung ab (siehe `test_consumer.py` für die Konsumentenlogik, Live-Smoke-Test für die Ende-zu-Ende-Verdrahtung). Seit P7-S3b zusätzlich: `/cases/due-for-archival`, `/case-archival-config`-Roundtrip, `409` bei Aussonderungsanfrage für eine offene Umlaufmappe, vollständiger Trigger→Rückruf-Roundtrip für eine über `session`/`repository` direkt geschlossene Umlaufmappe (kein voller BPMN-Durchlauf nötig, um nur die Aussonderungs-Endpunkte zu testen).
- **Live-Smoke-Test**: `docker compose build case-service document-service` + `up -d`, BPMN-Prozess mit Manual Task hochgeladen, Umlaufmappe angelegt (`process_instance_id` gesetzt), zwei Dokumente referenziert, neue Dokumentversion eingecheckt (dynamische Referenz bestätigt: `GET .../documents` zeigt die neue Version), Manual Task abgeschlossen, kurz gewartet, Abschluss bestätigt (`status="closed"`, `snapshot_version_number` fixiert, weitere Versionsänderungen am Original wirken sich nicht mehr aus) — Testdaten anschließend gelöscht.
- Reine Backend-Session, kein Browser-Test nötig (nicht in der UI-Sessions-Liste von `IMPLEMENTATION_PLAN.md`).

## Offene Punkte

- ~~Keine Rollenprüfung/RBAC~~ — **behoben in Post-Roadmap Phase 19 Session 5** ([ADR 0070](../adr/0070-case-service-rbac.md)): alle menschlich nutzbaren Endpunkte prüfen jetzt `case.read`/`case.write` gegen `permission-service` (`X-DMS-Principal` erforderlich). Die beiden rein internen Maschine-zu-Maschine-Rückrufe (`GET /cases/due-for-archival`, `PUT /cases/{id}/archived`) bleiben bewusst ungegatet, siehe ADR 0070 "Entscheidung".
- **Kein Ressourcen-Baumeintrag in permission-service** (weiterhin offen, seit P19-S5 als bewusster Kompromiss dokumentiert - ADR 0070): anders als folder-service registriert case-service keine `resource.created`/`.moved`/`.deleted`-Events — es gibt keinen bestehenden Präzedenzfall für Nicht-Ordner-Objekte im permission-service-Baum (document-service registriert seine Dokumente ebenfalls nicht einzeln), und eine Umlaufmappe hat ohnehin keinen Ordner-Elternknoten. Die neue RBAC-Prüfung (siehe oben) nutzt deshalb einheitlich `resource_id="root"` statt einer Umlaufmappen-eigenen Ressource — eine feingranulare Steuerung je Umlaufmappe bräuchte diese Baumstruktur zuerst.
- **Race-Bedingung bei vollständig automatisierten Prozessen ohne Manual Task** — siehe "Abschluss-Snapshot" oben. Nicht behoben (außerhalb des Kernumfangs dieser Session), Umlaufmappen mit einem synchron beim Start bereits abgeschlossenen Prozess bleiben in diesem seltenen Fall fälschlich `"open"`.
- **Prozessspezifische Bearbeitungskopien (2.3)** leben bewusst nicht hier, sondern als Erweiterung von document-service (`derived_from_document_id`/`derived_from_version_number`/`originating_case_id` an `POST /documents`, seit dieser Session) — siehe `docs/services/document-service.md` "Bearbeitungskopien". case-service selbst legt keine Bearbeitungskopien an.
- **Keine Existenzprüfung für `object_type_id` außerhalb der Validierung** — wie bei document-service/folder-service, keine Rückwirkungsprüfung bei nachträglich geänderten Objekttyp-Constraints.
- **Kein Cross-Phase-Bezug zu P15-S3 bereits verdrahtet** — die dortige Posteingang/Poststelle-Funktion wird laut Planung auf dieser Umlaufmappen-API aufbauen, ist aber nicht Teil dieser Session.
- ~~Kein Rollen-/Berechtigungscheck auf den neuen Aussonderungs-Endpunkten~~ (5.6, seit P7-S3b) — **teilweise behoben in P19-S5** (ADR 0070): `POST /cases/{id}/archive-request` (menschliche Aktion) ist jetzt gegated (`case.write`). `PUT /cases/{id}/archived` (interner Rückruf von `archival-service`, kein menschlicher Aufrufer) bleibt bewusst ungegatet — konsistent mit `document-service`s eigenem, ebenfalls ungegateten `PUT /documents/{id}/archived`.
- **Kein Retry für eine fehlgeschlagene Aussonderung** — ein `failed`-`CaseArchivalTransfer` in `archival-service` bleibt terminal, ein erneuter `POST /cases/{id}/archive-request` hier würde am aktiven-Transfer-Ausschluss dort scheitern, solange die alte Zeile nicht separat behandelt wird.
