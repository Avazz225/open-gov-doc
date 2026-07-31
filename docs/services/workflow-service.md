# workflow-service

**Verantwortung:** Workflow Engine Grundgerüst (Konzept 7.1) — BPMN-2.0-Import und -Ausführung über [SpiffWorkflow](https://github.com/sartography/SpiffWorkflow) (LGPLv3, [ADR 0018](../adr/0018-spiffworkflow-lgpl-license.md)), Manual/Automatic Tasks. Reines Engine-Grundgerüst dieser Session — kein UI (Process Designer folgt mit P6-S6), keine Rollenprüfung (folgt mit P6-S4).

**Konzept-Referenz:** 7.1
**Eigenes Postgres-Schema:** `workflow` (Tabellen `process_definition`, `process_instance`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/process-definitions` | Anlegen (multipart: `bpmn_xml` Datei, `name`, optional `process_id`) — parst die BPMN-XML über SpiffWorkflow; ohne `process_id` wird automatisch aufgelöst, aber nur wenn die Datei genau einen ausführbaren Top-Level-Prozess enthält. `409` bei Namenskollision, `422` bei nicht parsbarer/mehrdeutiger BPMN-Datei |
| `GET` | `/process-definitions` | Liste (nur Metadaten, ohne `bpmn_xml`) |
| `GET` | `/process-definitions/{id}` | Detail inkl. `bpmn_xml` — `404` |
| `DELETE` | `/process-definitions/{id}` | Löschen — `409` falls noch Prozessinstanzen existieren, sonst `204` |
| `POST` | `/process-definitions/{id}/instances` | Instanz starten (`created_by`, optional `business_key`, `initial_data`) — führt alle bereiten automatischen Tasks sofort aus (`do_engine_steps()`), Status ist `"completed"`, wenn der Prozess dabei ohne Manual Task durchläuft, sonst `"running"` |
| `GET` | `/instances/{id}` | Status/Metadaten — `404` |
| `GET` | `/instances?process_definition_id=&status=&business_key=` | Gefilterte Liste |
| `GET` | `/instances/{id}/tasks` | Aktuell bereite Manual/User Tasks (`id`, `name`, `lane`, `data`) |
| `POST` | `/instances/{id}/tasks/{task_id}/complete` | Task abschließen (`completed_by`, optional `data`) — `404` bei unbekannter Instanz, `409` wenn `task_id` nicht (mehr) bereit ist (bereits abgeschlossen, falsche ID) |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `process_definition`: `id`, `name` (unique, vom Aufrufer vergebener Anzeigename), `bpmn_process_id` (die interne Prozess-ID aus der BPMN-XML selbst, `<bpmn:process id="...">`), `bpmn_xml` (Text, die vollständige hochgeladene Datei), `created_at`/`updated_at`. **Keine Versionierung**: ein erneuter Upload unter demselben `name` wird mit `409` abgelehnt, nicht als neue Version angelegt (siehe "Offene Punkte").
- `process_instance`: `id` (UUID), `process_definition_id` (FK), `business_key` (String, nullable, opake Cross-Service-Referenz z. B. auf eine künftige `document_id` — **nicht** gegen den Document Service validiert, gleiches Muster wie `folder_id`/`object_type_id` vor ihrer jeweiligen Durchsetzung in früheren Phasen), `status` (`"running"`\|`"completed"`), `workflow_state` (Text — vollständiger, von SpiffWorkflow serialisierter Ausführungszustand, siehe "State-Persistenz" unten), `created_by`, `created_at`/`updated_at`/`completed_at` (nullable).

## SpiffWorkflow-Anbindung (`spiff_adapter.py`)

Die gesamte SpiffWorkflow-API-Oberfläche ist in einem einzigen Modul isoliert (`src/workflow_service/spiff_adapter.py`) — `repository.py` kennt SpiffWorkflow-Klassen nicht direkt, nur die eigenen Wrapper-Funktionen. Grund: SpiffWorkflows API ist nicht formal als stabil dokumentiert; ein künftiger Versions-Bump muss so nur an einer Stelle nachgezogen werden. Gegen die tatsächlich installierte Version (**3.1.2**) per `help()`/`inspect` verifiziert, nicht nur aus der Dokumentation übernommen — u. a. wurde dabei empirisch festgestellt, dass `BpmnWorkflow.set_data()` **nicht** ausreicht, um Prozessvariablen beim Start sichtbar zu machen (Daten werden beim Abschluss eines Tasks an dessen Kinder weitergereicht, nicht rückwirkend aus dem workflow-weiten `data`-Dict gelesen) — `initial_data` wird deshalb direkt auf dem zu Beginn bereiten Start-Task gesetzt.

- **Manual/User Tasks** (`<bpmn:manualTask>`/`<bpmn:userTask>`, beide haben `task_spec.manual == True`): bleiben nach `do_engine_steps()` bereit stehen, bis `POST .../complete` aufgerufen wird. `task_spec.lane` (Bahn-/Rollenname aus dem BPMN-Modell, `None` falls das Modell keine Lanes definiert) wird informativ mitgeliefert — **keine Auswertung/Durchsetzung** in dieser Session, siehe "Offene Punkte".
- **Automatic Tasks (Script Tasks)**: laufen über SpiffWorkflows eingebaute Standard-Python-Scripting-Umgebung automatisch, sobald sie bereit sind — keine eigene Connector-/Delegate-Registrierung in diesem Grundgerüst.

## State-Persistenz (ADR 0019)

Jede Prozessinstanz speichert ausschließlich den vollständigen, von `BpmnWorkflowSerializer.serialize_json()` erzeugten JSON-Blob — **keine** separate, normalisierte Task-Tabelle. Begründung/Konsequenzen: siehe [ADR 0019](../adr/0019-workflow-full-state-serialization.md).

## Events

**Publiziert** (Stream `workflow`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `workflow.instance.started` | `{process_definition_id, created_by}` |
| `workflow.instance.completed` | `{business_key}` |
| `workflow.task.completed` | `{task_id, completed_by}` |

**Konsumiert:** keine — reiner Producer in dieser Session. Kein bestehender Service emittiert aktuell etwas, worauf workflow-service reagieren müsste (z. B. der `needs_review`-Flag aus `ocr.completed`, siehe `docs/services/ocr-service.md` "Offene Punkte") — diese Anbindung ist explizit auf eine spätere Phase-6-Session verschoben, vermutlich zusammen mit dem generischen Approval-Mechanismus (P6-S4).

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`), identisches Muster wie jeder andere Service. Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

`uv run pytest services/workflow-service/tests` (44 Tests, alle grün):
- `test_spiff_adapter.py` — isolierter Test des SpiffWorkflow-Wrappers gegen echte BPMN-Test-Fixtures (`tests/fixtures/`, aus dem offiziellen SpiffWorkflow-GitHub-Repo übernommen, nicht handgeschrieben, um Namespace-/Schema-Fehler zu vermeiden): Parsing/Auto-Erkennung der Prozess-ID, Ausführung eines Script Tasks parallel zu einem wartenden Manual Task, Serialisierungs-Rundreise (Task-ID bleibt stabil), Task-Abschluss bis zur vollständigen Prozessbeendigung, Lane-Extraktion mit und ohne BPMN-Lanes im Modell.
- `test_repository.py`/`test_api.py` — Anlegen+Duplikat-/Ungültig-Fälle, Instanzstart (mit wartendem Manual Task vs. vollautomatisch sofort abgeschlossen), bereite Tasks auflisten, Task abschließen bis zum Abschluss der Instanz, doppeltes Abschließen desselben Tasks abgelehnt, Löschung einer referenzierten Prozessdefinition abgelehnt, Filterung der Instanzliste nach Status.
- Läuft wie jeder andere Service gegen echte Infrastruktur (Postgres, NATS) — kein Mocking. Wie immer: niemals gegen die laufende Entwicklungs-Datenbank, siehe `PROGRESS.md` "Tooling & Testing".
- **Live-Smoke-Test**: `docker compose build workflow-service` + `up -d`, echte BPMN-Datei über curl hochgeladen, Instanz gestartet, bereite Tasks aufgelistet, Task abgeschlossen, Instanzstatus `"completed"` bestätigt — Testdaten anschließend gelöscht.
- Reine Backend-Session, kein Browser-Test nötig (nicht in der UI-Sessions-Liste von `IMPLEMENTATION_PLAN.md`).

## Offene Punkte

- **Keine Rollenprüfung/RBAC** — weder ein `X-DMS-Roles`-Stringcheck (wie beim Kennzeichen-Feature, P5e-S2) noch ein echter `permission-service`-Aufruf. `completed_by`/`created_by` sind reine, ungeprüfte Strings. Explizit **P6-S4** zugewiesen (genereller Vier-Augen-Approval-Mechanismus + Superuser Break-Glass).
- **Script Tasks führen serverseitig beliebigen, in der BPMN-XML eingebetteten Python-Code aus** (SpiffWorkflows Standard-Scripting-Umgebung) — ohne Rollenprüfung am Upload-Endpunkt ein reales Sicherheitsthema, sobald BPMN-Import nicht mehr nur von vertrauenswürdigen internen Nutzern erfolgt (7.1 sieht Import aus externen Werkzeugen als Kernfeature vor). Bewusste Entscheidung nach Rückfrage: für den aktuellen internen Test-/Entwicklungsbetrieb aktiviert, als offener Punkt an P6-S4 verwiesen statt vorab eingeschränkt oder als No-Op-Stub gebaut.
- **Keine SLA-/Zeitüberwachung** (Timer/Boundary Events), keine Eskalation, kein Notification Service — **P6-S2**.
- **Keine Case-Service-Anbindung** (Umlaufmappen, prozessspezifische Bearbeitungskopien, 2.3) — **P6-S3**.
- **Keine Signature Tasks** (3.10) — **P6-S5**.
- **Kein Process Designer** — Prozesse können ausschließlich per BPMN-XML-Upload importiert werden, keine grafische Modellierung im System selbst — **P6-S6** (eigenständige Frontend-Anwendung mit `bpmn-js`).
- **Keine föderierten/installationsübergreifenden Prozessschritte** (7.4) — spätere Phase.
- **Import-Validierung ist rein strukturell** (SpiffWorkflow kann die Datei parsen, Prozess-ID auflösbar) — die in 7.1 beschriebene tiefere Validierung gegen im System verfügbare Objekttypen/Ordnerziele/Instanz-Ziele setzt Task-Typen voraus, die es in diesem Grundgerüst noch nicht gibt (z. B. ein "Dokument anlegen"-Task-Typ).
- **`business_key` ist eine unvalidierte opake Referenz** — kein Abgleich gegen den Document Service, ob eine angegebene `document_id` tatsächlich existiert.
- **Keine Prozess-Versionierung** — ein erneuter Upload unter demselben `name` wird abgelehnt (`409`), nicht als neue Version neben bereits laufenden Instanzen der alten Version geführt.
- **Kein Rückwirkungs-Check beim Löschen einer Prozessdefinition auf Task-Ebene** — nur die Existenz irgendeiner Instanz wird geprüft, nicht ob diese noch tatsächlich läuft.
