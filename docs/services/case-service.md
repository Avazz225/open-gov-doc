# case-service

**Verantwortung:** Umlaufmappen (Konzept 2.3) — bündeln Referenzen (keine eigenen Kopien) auf Dokumente, die zu einem Vorgang gehören. Eigener Lebenszyklus über eine Prozessinstanz in [workflow-service](../adr/0018-spiffworkflow-lgpl-license.md) (7.1, P6-S1): während die Umlaufmappe offen ist, wird je referenziertem Dokument dynamisch die aktuellste Version aufgelöst; beim Erreichen des BPMN-Endzustands wird die Referenzstruktur als **Abschluss-Snapshot** fixiert. Kein UI, keine Rollenprüfung (siehe "Offene Punkte").

**Konzept-Referenz:** 2.3
**Eigenes Postgres-Schema:** `case` (Tabellen `cases`, `case_document_reference`) — `"case"` ist ein reserviertes SQL-Schlüsselwort (`CASE WHEN`); SQLAlchemy quotet es in generierten DDL-Statements automatisch, rohe SQL-Strings (`main.py`, `tests/conftest.py`) müssen es selbst als `"case"` quoten. Die Tabelle für Umlaufmappen heißt deshalb bewusst `cases` (Plural), nicht `case`, um diese Quoting-Pflicht nicht doppelt zu brauchen.

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/cases` | Anlegen (`name`, optional `object_type_id`/`attributes`, `process_definition_id`, `created_by`, optional `initial_data`) — validiert `object_type_id` (falls gesetzt) gegen den Object-Type Service (immer als Wurzel-Objekt, keine Ordner-Elternschaft), startet danach eine Prozessinstanz in workflow-service mit `business_key = case_id`. `400` bei unbekanntem `process_definition_id` |
| `GET` | `/cases` | Liste, Filter `status`/`object_type_id` |
| `GET` | `/cases/{id}` | Detail — `404` |
| `POST` | `/cases/{id}/documents` | Dokumentreferenz hinzufügen (`document_id`, `added_by`) — `400` falls `document_id` laut Document Service unbekannt, `404` falls die Umlaufmappe unbekannt ist, `409` falls sie bereits abgeschlossen ist |
| `DELETE` | `/cases/{id}/documents/{document_id}` | Referenz weich entfernen (`removed_by`) — Zeile bleibt erhalten (Nachvollziehbarkeit), `404`/`409` analog |
| `GET` | `/cases/{id}/documents` | Alle Referenzen (aktiv + entfernt) inkl. aufgelöster Version: offen → live `current_version_number`/`deleted_at` aus dem Document Service, geschlossen → fixierter `snapshot_version_number`, kein Document-Service-Aufruf mehr nötig |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `cases`: `id` (UUID), `name`, `object_type_id` (opake Referenz, optional), `attributes` (JSON), `status` (`"open"`\|`"closed"`), `process_definition_id`/`process_instance_id` (opake Referenzen auf workflow-service), `created_by`/`created_at`, `closed_at` (nullable).
- `case_document_reference`: `id`, `case_id` (FK), `document_id` (opake Referenz auf document-service), `added_by`/`added_at`, `removed_by`/`removed_at` (beide nullable — weiche Löschung statt Hard-Delete), `snapshot_version_number` (nullable, nur nach Abschluss gesetzt).

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

## Events

**Publiziert** (Stream `case`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `case.created` | `{name, created_by}` |
| `case.document.added` | `{document_id, added_by}` |
| `case.document.removed` | `{document_id, removed_by}` |
| `case.closed` | `{process_instance_id}` |

**Konsumiert:** `workflow.instance.completed` (siehe "Abschluss-Snapshot" oben).

**Audit-Anbindung**: Audit Service konsumiert seit dieser Session zusätzlich `case.>` (gleiches Sofort-Ergänzungs-Muster wie bei jedem vorherigen neuen Producer-Stream).

## Selbst-Registrierung (Konzept 3.2a)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`), identisches Muster wie jeder andere Service. Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`. Das Gateway benötigt keine eigene Codeänderung — Routing läuft vollständig dynamisch über `service_type="case-service"`.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

`uv run pytest services/case-service/tests`:
- `test_repository.py` — reine DB-Logik (Anlegen, Referenzen hinzufügen/entfernen, Abschluss-Snapshot inkl. Randfälle: entfernte Referenz bleibt ohne Snapshot, fehlende `document_id` im Snapshot-Dict bleibt ohne Snapshot) — läuft gegen echte Postgres wie überall im Projekt, keine HTTP-Aufrufe (`repository.py` kennt keine Sibling-Services).
- `test_consumer.py` — simuliertes `workflow.instance.completed`-Event direkt an den Handler (kein echtes NATS nötig, gleiches Muster wie `notification-service/tests/test_consumer.py`), Fake-`DocumentClient` statt echtem HTTP.
- `test_api.py` — echte Integrationstests gegen lokal erreichbare `workflow-service`-/`document-service`-/`object-type-service`-Instanzen (gleiches Muster wie document-services `folder_client`/`object_type_client`-Tests) — jeder Testfall legt sich seine eigene Prozessdefinition/sein eigenes Testdokument an. Deckt bewusst **nicht** die tatsächliche asynchrone Event-Zustellung ab (siehe `test_consumer.py` für die Konsumentenlogik, Live-Smoke-Test für die Ende-zu-Ende-Verdrahtung).
- **Live-Smoke-Test**: `docker compose build case-service document-service` + `up -d`, BPMN-Prozess mit Manual Task hochgeladen, Umlaufmappe angelegt (`process_instance_id` gesetzt), zwei Dokumente referenziert, neue Dokumentversion eingecheckt (dynamische Referenz bestätigt: `GET .../documents` zeigt die neue Version), Manual Task abgeschlossen, kurz gewartet, Abschluss bestätigt (`status="closed"`, `snapshot_version_number` fixiert, weitere Versionsänderungen am Original wirken sich nicht mehr aus) — Testdaten anschließend gelöscht.
- Reine Backend-Session, kein Browser-Test nötig (nicht in der UI-Sessions-Liste von `IMPLEMENTATION_PLAN.md`).

## Offene Punkte

- **Keine Rollenprüfung/RBAC** — weder `X-DMS-Roles`-Stringcheck noch echter `permission-service`-Aufruf, wie bei jedem Service vor seiner jeweiligen Durchsetzung. Provisorisch der P6-S4/S5/S6-Familie zugeordnet (siehe `PROGRESS.md` "Autorisierung").
- **Kein Ressourcen-Baumeintrag in permission-service**: anders als folder-service registriert case-service keine `resource.created`/`.moved`/`.deleted`-Events — es gibt keinen bestehenden Präzedenzfall für Nicht-Ordner-Objekte im permission-service-Baum (document-service registriert seine Dokumente ebenfalls nicht einzeln), und eine Umlaufmappe hat ohnehin keinen Ordner-Elternknoten.
- **Race-Bedingung bei vollständig automatisierten Prozessen ohne Manual Task** — siehe "Abschluss-Snapshot" oben. Nicht behoben (außerhalb des Kernumfangs dieser Session), Umlaufmappen mit einem synchron beim Start bereits abgeschlossenen Prozess bleiben in diesem seltenen Fall fälschlich `"open"`.
- **Prozessspezifische Bearbeitungskopien (2.3)** leben bewusst nicht hier, sondern als Erweiterung von document-service (`derived_from_document_id`/`derived_from_version_number`/`originating_case_id` an `POST /documents`, seit dieser Session) — siehe `docs/services/document-service.md` "Bearbeitungskopien". case-service selbst legt keine Bearbeitungskopien an.
- **Keine Existenzprüfung für `object_type_id` außerhalb der Validierung** — wie bei document-service/folder-service, keine Rückwirkungsprüfung bei nachträglich geänderten Objekttyp-Constraints.
- **Kein Cross-Phase-Bezug zu P15-S3 bereits verdrahtet** — die dortige Posteingang/Poststelle-Funktion wird laut Planung auf dieser Umlaufmappen-API aufbauen, ist aber nicht Teil dieser Session.
