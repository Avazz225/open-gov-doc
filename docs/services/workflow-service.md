# workflow-service

**Verantwortung:** Workflow Engine Grundgerüst (Konzept 7.1) — BPMN-2.0-Import und -Ausführung über [SpiffWorkflow](https://github.com/sartography/SpiffWorkflow) (LGPLv3, [ADR 0018](../adr/0018-spiffworkflow-lgpl-license.md)), Manual/Automatic Tasks, seit P6-S2 auch Timer/Boundary Events (SLA-Zeitüberwachung, [ADR 0020](../adr/0020-sla-timer-polling.md)), seit **P6-S7** zusätzlich der "Signature Task" (3.10, technisch eine Sonderform des Manual Task). Kein UI (Process Designer folgt mit P6-S8). Seit **P6-S6** teilweise gegated: Prozessdefinitionen anlegen/löschen erfordert die Capability `admin.object_config` (4.6-Retrofit), Instanzstart/Task-Abschluss bleiben für jeden authentifizierten Principal offen, respektieren aber den systemweiten Wartungsmodus (Not-Shutdown, 4.8) — siehe [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md).

**Konzept-Referenz:** 7.1, 4.6, 4.8, 3.10
**Eigenes Postgres-Schema:** `workflow` (Tabellen `process_definition`, `process_instance`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/process-definitions` | Anlegen (multipart: `bpmn_xml` Datei, `name`, optional `process_id`) — parst die BPMN-XML über SpiffWorkflow; ohne `process_id` wird automatisch aufgelöst, aber nur wenn die Datei genau einen ausführbaren Top-Level-Prozess enthält. `422` bei nicht parsbarer/mehrdeutiger BPMN-Datei. **Seit P6-S6**: erfordert `X-DMS-Principal` mit der Capability `admin.object_config` (4.6), sonst `403`. **Seit P6-S8**: `name` ist der Prozessfamilien-Schlüssel — ein bereits existierender Name erzeugt automatisch die nächste `version` statt `409` (siehe "Versionierung" unten) |
| `GET` | `/process-definitions?name=` | Ohne `name`: neueste Version je Prozessfamilie. **Seit P6-S8** mit `name`: vollständige Versionshistorie dieser Familie, neueste zuerst (nur Metadaten, ohne `bpmn_xml`) |
| `GET` | `/process-definitions/{id}` | Detail inkl. `bpmn_xml` — `404` |
| `DELETE` | `/process-definitions/{id}` | Löschen — `409` falls noch Prozessinstanzen existieren, sonst `204`. **Seit P6-S6**: gegated wie `POST /process-definitions` |
| `POST` | `/process-definitions/{id}/instances` | Instanz starten (`created_by`, optional `business_key`, `initial_data`) — führt alle bereiten automatischen Tasks sofort aus (`do_engine_steps()`), Status ist `"completed"`, wenn der Prozess dabei ohne Manual Task durchläuft, sonst `"running"`. **Seit P6-S6**: `503` bei aktivem `X-DMS-Maintenance-Active`-Header (4.8), sonst weiterhin offen für jeden authentifizierten Principal |
| `GET` | `/instances/{id}` | Status/Metadaten — `404` |
| `GET` | `/instances?process_definition_id=&status=&business_key=` | Gefilterte Liste |
| `GET` | `/instances/{id}/tasks` | Aktuell bereite Manual/User Tasks (`id`, `name`, `lane`, `data`, seit **P6-S7** zusätzlich `extensions` — Camunda-`extensionElements`-Properties, leer bei gewöhnlichen Manual Tasks, `{taskType: "signature", requiredLevel: ...}` bei einem Signature Task) |
| `POST` | `/instances/{id}/tasks/{task_id}/complete` | Task abschließen (`completed_by`, optional `data`, seit **P6-S7** optional `signature_id`) — `404` bei unbekannter Instanz, `409` wenn `task_id` nicht (mehr) bereit ist (bereits abgeschlossen, falsche ID). **Seit P6-S6**: `503` bei aktivem Wartungsmodus, gleiches Muster wie Instanzstart. **Seit P6-S7**: ist die Task laut `extensions` ein Signature Task, ist `signature_id` Pflicht und muss auf eine beim `signature-service` existierende, zum Task-Dokument passende Signatur mit ausreichendem Niveau verweisen — sonst `400` (siehe "Signature Task" unten) |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

- `process_definition`: `id`, `name` (seit P6-S8 der Prozessfamilien-Schlüssel, nicht mehr global eindeutig, siehe "Versionierung" unten), `version` (Integer, Default 1, eindeutig zusammen mit `name`), `bpmn_process_id` (die interne Prozess-ID aus der BPMN-XML selbst, `<bpmn:process id="...">`), `bpmn_xml` (Text, die vollständige hochgeladene Datei), `created_at`/`updated_at`.
- `process_instance`: `id` (UUID), `process_definition_id` (FK), `business_key` (String, nullable, opake Cross-Service-Referenz z. B. auf eine künftige `document_id` — **nicht** gegen den Document Service validiert, gleiches Muster wie `folder_id`/`object_type_id` vor ihrer jeweiligen Durchsetzung in früheren Phasen), `status` (`"running"`\|`"completed"`), `workflow_state` (Text — vollständiger, von SpiffWorkflow serialisierter Ausführungszustand, siehe "State-Persistenz" unten), `created_by`, `created_at`/`updated_at`/`completed_at` (nullable).

## Versionierung (seit P6-S8, ADR 0027)

`name` ist der Prozessfamilien-Schlüssel: `POST /process-definitions` unter einem bereits existierenden Namen legt automatisch die nächste `version` an (`max(version je name) + 1`), statt mit `409` abgelehnt zu werden — zwei Definitionen mit demselben Namen sind der Normalfall. `GET /process-definitions` liefert per Default nur die neueste Version je Familie (`DISTINCT ON`), `GET /process-definitions?name=X` die vollständige Historie. Prozessinstanzen bleiben unverändert an eine konkrete `id` (also eine bestimmte Version) gebunden — eine neue Version wirkt sich nie rückwirkend auf bereits laufende oder abgeschlossene Instanzen aus, `POST .../{id}/instances`/`DELETE /process-definitions/{id}` mussten dafür nicht geändert werden. Grundlage für den "Speichern"-Fluss des neuen Process Designer (`apps/process-designer`, P6-S8): Name unverändert lassen erzeugt eine neue Version, ein geänderter Name eine neue Familie.

## SpiffWorkflow-Anbindung (`spiff_adapter.py`)

Die gesamte SpiffWorkflow-API-Oberfläche ist in einem einzigen Modul isoliert (`src/workflow_service/spiff_adapter.py`) — `repository.py` kennt SpiffWorkflow-Klassen nicht direkt, nur die eigenen Wrapper-Funktionen. Grund: SpiffWorkflows API ist nicht formal als stabil dokumentiert; ein künftiger Versions-Bump muss so nur an einer Stelle nachgezogen werden. Gegen die tatsächlich installierte Version (**3.1.2**) per `help()`/`inspect` verifiziert, nicht nur aus der Dokumentation übernommen — u. a. wurde dabei empirisch festgestellt, dass `BpmnWorkflow.set_data()` **nicht** ausreicht, um Prozessvariablen beim Start sichtbar zu machen (Daten werden beim Abschluss eines Tasks an dessen Kinder weitergereicht, nicht rückwirkend aus dem workflow-weiten `data`-Dict gelesen) — `initial_data` wird deshalb direkt auf dem zu Beginn bereiten Start-Task gesetzt.

- **Manual/User Tasks** (`<bpmn:manualTask>`/`<bpmn:userTask>`, beide haben `task_spec.manual == True`): bleiben nach `do_engine_steps()` bereit stehen, bis `POST .../complete` aufgerufen wird. `task_spec.lane` (Bahn-/Rollenname aus dem BPMN-Modell, `None` falls das Modell keine Lanes definiert) wird informativ mitgeliefert — **keine Auswertung/Durchsetzung** in dieser Session, siehe "Offene Punkte".
- **Automatic Tasks (Script Tasks)**: laufen über SpiffWorkflows eingebaute Standard-Python-Scripting-Umgebung automatisch, sobald sie bereit sind — keine eigene Connector-/Delegate-Registrierung in diesem Grundgerüst.
- **Timer/Boundary Events (P6-S2, SLA-Zeitüberwachung, 7.1)**: `spiff_adapter.check_timers()` kapselt `wf.refresh_waiting_tasks()`+`do_engine_steps()` und meldet gefeuerte Boundary-Timer (erkannt über `isinstance(task_spec, BoundaryEvent)`) zurück. Beide BPMN-Semantiken real gegen die installierte Version getestet: non-interrupting (`cancelActivity="false"`) lässt den ursprünglichen Task weiterlaufen, interrupting (`cancelActivity="true"`, BPMN-Default) storniert ihn — beides vollständig SpiffWorkflow-eigene Semantik, siehe Modul-Docstring.

**Seit P6-S7**: Parser auf `SpiffWorkflow.camunda.parser.CamundaParser` umgestellt (weiterhin identische `add_bpmn_str`/`get_process_ids`/`get_spec`-Oberfläche, `manualTask` mappt unverändert auf `ManualTask`), da nur dieser Parser `bpmn:extensionElements/camunda:properties` in `task_spec.extensions` einliest (Grundlage des Signature Task, siehe unten). Musste von einem passenden Serializer-Config begleitet werden: `CamundaParser` mappt `<bpmn:userTask>` zusätzlich auf Camundas eigene `UserTask`-Spec-Klasse statt der BPMN-Default-Klasse — `_SERIALIZER` nutzt seither `SpiffWorkflow.camunda.serializer.config.CAMUNDA_CONFIG` statt `BpmnWorkflowSerializer.configure()`, sonst schlägt die JSON-Serialisierung bereits bestehender `userTask`-Fixtures (z. B. der P6-S2-Boundary-Timer-Test) fehl. Alle 52 vorherigen Tests bleiben nach diesem Wechsel unverändert grün (siehe [ADR 0025](../adr/0025-signature-service-internal-ca-and-connector-plugin.md)).

## State-Persistenz (ADR 0019)

Jede Prozessinstanz speichert ausschließlich den vollständigen, von `BpmnWorkflowSerializer.serialize_json()` erzeugten JSON-Blob — **keine** separate, normalisierte Task-Tabelle. Begründung/Konsequenzen: siehe [ADR 0019](../adr/0019-workflow-full-state-serialization.md).

## SLA-Poll-Loop (P6-S2, ADR 0020)

Ein asyncio-Hintergrund-Task (`_sla_poll_loop` in `main.py`, gestartet in `lifespan`) prüft alle `sla_poll_interval_seconds` (Default 30s, `DMS_SLA_POLL_INTERVAL_SECONDS`) **jede** Instanz mit `status="running"`: deserialisieren, `spiff_adapter.check_timers()`, Blob neu persistieren, gefeuerte Boundary-Events als `workflow.task.escalated` publizieren. Kein Push-Mechanismus, keine verteilte Sperre bei mehreren `workflow-service`-Replikaten — siehe [ADR 0020](../adr/0020-sla-timer-polling.md) für die vollständige Begründung und die dokumentierten Grenzen. **Seit P6-S6**: prüft vor jedem Tick zusätzlich über den neuen `permission_client.py` (`is_maintenance_active()`, `GET /maintenance-mode` am Permission Service) direkt, ob der systemweite Wartungsmodus (4.8) aktiv ist, und überspringt den Tick dann komplett — kein eingehender Request, an den das Gateway einen `X-DMS-Maintenance-Active`-Header hängen könnte, daher die einzige Stelle in diesem Service mit einer eigenen Polling-Verbindung zum Permission Service statt Header-Auswertung.

## Autorisierung & Wartungsmodus (4.6/4.8, seit P6-S6)

Bewusst begrenzter Retrofit (Nutzerentscheidung: "Admin-Aktionen gaten, Alltagsnutzung offen", siehe [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md)):

- **Prozessdefinitionen anlegen/löschen** (BPMN-/Script-Task-Upload) erfordern die Capability `admin.object_config` — echter Aufruf gegen den Permission Service über einen neuen, dünnen `permission_client.py` (gleiches Muster wie `auth-service`s Rollen-Client aus P6-S5), `403` ohne die Capability oder ohne `X-DMS-Principal`-Header. `auth-service` legt dafür ein zweites technisches Konto `config-admin`/`config-admin` an (Domäne "Workflow-Konfiguration"). Da Skript-Code ausschließlich über diesen jetzt gegateten Upload ins System gelangt (keine separate Ausführungs-Route), sichert das faktisch auch die Script-Task-Ausführung ab, ohne einen eigenen Check dafür zu brauchen.
- **Instanzstart/Task-Abschluss bleiben bewusst ungegatet** für jeden authentifizierten Principal — normale Fachnutzung soll keine Domain-Admin-Rolle brauchen. Stattdessen respektieren beide Endpunkte den neuen systemweiten Wartungsmodus (4.8): `503`, solange der vom Gateway injizierte `X-DMS-Maintenance-Active`-Header `"true"` ist.
- **`created_by`/`completed_by` bleiben weiterhin reine, ungeprüfte Strings** — die Gating-Entscheidung betrifft nur, *ob* eine Aktion ausgeführt werden darf, nicht, ob der angegebene Name stimmt (unverändert seit P6-S1).

## Signature Task (3.10, seit P6-S7)

Ein `<bpmn:manualTask>` mit `bpmn:extensionElements/camunda:properties` `taskType=signature`/`requiredLevel=ses|aes|qes` wird als Signature Task erkannt — technisch bleibt es ein gewöhnlicher Manual Task (`.manual is True`, `ready_manual_tasks`/`find_ready_task` unverändert), fachlich verlangt der Abschluss aber eine echte Signatur:

- Die zu signierende `document_id` läuft über die bestehende generische `data`-Prozessvariable (kein neues Schema-Feld — workflow-service kennt kein Dokument-Konzept), z. B. per `initial_data` beim Instanzstart gesetzt.
- `POST .../tasks/{id}/complete` verlangt in diesem Fall `signature_id` im Body. Ein neuer, dünner `signature_client.py` (Muster wie `permission_client.py` aus P6-S6) fragt beim `signature-service` (`GET /signatures/{id}`) nach: die Signatur muss existieren, zum `document_id`-Wert der Task-Daten passen und mindestens `requiredLevel` erreichen (Rangfolge `ses < aes < qes`) — sonst `400`.
- Kein neues BPMN-Element, keine Modeler-Tooling-Änderung nötig — passend zur Konzeptformulierung "technisch eine Sonderform des Manual Task". Beispiel-Fixture: `tests/fixtures/signature_task.bpmn`.
- **Kein Prozess-Designer-Palette-Eintrag** in dieser Session — ein Signature Task muss die Extension-Attribute von Hand im hochgeladenen BPMN-XML setzen. Folgt mit P6-S8.

## Events

**Publiziert** (Stream `workflow`, `ensure_stream=True`):

| event_type | payload |
|---|---|
| `workflow.instance.started` | `{process_definition_id, created_by}` |
| `workflow.instance.completed` | `{business_key}` |
| `workflow.task.completed` | `{task_id, completed_by}` |
| `workflow.task.escalated` | `{process_definition_id, business_key, task_name, lane, escalation_email}` (P6-S2, gefeuert vom SLA-Poll-Loop bei einem ausgelösten Boundary-Timer; `escalation_email` ist ein opakes Prozessdatum aus `initial_data`, siehe SLA-Poll-Loop-Abschnitt) |

**Konsumiert:** keine — reiner Producer. `notification-service` (P6-S2) ist der erste Konsument von `workflow.task.escalated`, `case-service` (P6-S3) der erste Konsument von `workflow.instance.completed` (löst dort den Abschluss-Snapshot einer Umlaufmappe aus, siehe `docs/services/case-service.md`), siehe je Service. Kein anderer bestehender Service emittiert aktuell etwas, worauf workflow-service reagieren müsste (z. B. der `needs_review`-Flag aus `ocr.completed`, siehe `docs/services/ocr-service.md` "Offene Punkte") — diese Anbindung bleibt auf eine spätere Phase-6-Session verschoben, vermutlich zusammen mit dem generischen Approval-Mechanismus (P6-S4).

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`), identisches Muster wie jeder andere Service. Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

`uv run pytest services/workflow-service/tests`:
- `test_spiff_adapter.py` — isolierter Test des SpiffWorkflow-Wrappers gegen echte BPMN-Test-Fixtures (`tests/fixtures/`, aus dem offiziellen SpiffWorkflow-GitHub-Repo übernommen bzw. — für den Signature Task — selbst geschrieben, siehe unten): Parsing/Auto-Erkennung der Prozess-ID, Ausführung eines Script Tasks parallel zu einem wartenden Manual Task, Serialisierungs-Rundreise (Task-ID bleibt stabil), Task-Abschluss bis zur vollständigen Prozessbeendigung, Lane-Extraktion mit und ohne BPMN-Lanes im Modell, seit P6-S2: `check_timers()` feuert einen non-interrupting Boundary-Timer, `escalation_email` aus `initial_data` ist im Datenschnappschuss sichtbar, der ursprüngliche Task bleibt danach normal abschließbar. Seit **P6-S7**: ein gewöhnlicher Manual Task ohne Extensions liefert ein leeres `extensions`-Dict, `signature_task.bpmn` liefert `{taskType: "signature", requiredLevel: "aes"}`.
- `test_repository.py`/`test_api.py` — Anlegen+Duplikat-/Ungültig-Fälle, Instanzstart (mit wartendem Manual Task vs. vollautomatisch sofort abgeschlossen), bereite Tasks auflisten, Task abschließen bis zum Abschluss der Instanz, doppeltes Abschließen desselben Tasks abgelehnt, Löschung einer referenzierten Prozessdefinition abgelehnt, Filterung der Instanzliste nach Status, seit P6-S2: `advance_timers()` feuert das Boundary-Event und persistiert den aktualisierten Blob, seit **P6-S6**: `create_process_definition`/`delete_process_definition` ohne `admin.object_config` → `403` (echter Aufruf gegen den live laufenden Permission Service, kein Mocking — ein session-scoped `admin_headers`-Fixture in `conftest.py` weist die Capability idempotent einem Test-Principal zu), Instanzstart/Task-Abschluss bei aktivem `X-DMS-Maintenance-Active`-Header → `503`. Seit **P6-S7**: `GET .../tasks` surfacet die Signature-Task-Extensions; Task-Abschluss ohne `signature_id`/mit unbekannter `signature_id`/mit zum falschen Dokument gehörender Signatur/mit zu niedrigem Niveau → jeweils `400` (echte Signaturen gegen den live laufenden `signature-service`/`document-service`/`auth-service` erzeugt, kein Mocking); mit einer echten, ausreichenden Signatur → `200`.
- Läuft wie jeder andere Service gegen echte Infrastruktur (Postgres, NATS) — kein Mocking. Wie immer: niemals gegen die laufende Entwicklungs-Datenbank, siehe `PROGRESS.md` "Tooling & Testing".
- Der volle Poll-Loop selbst (`_sla_poll_loop`) wird nicht per Sleep-Wartezeit in der Testsuite geprüft (nicht deterministisch genug) — stattdessen per Live-Smoke-Test mit kurzem `DMS_SLA_POLL_INTERVAL_SECONDS` gegen den gebauten Container verifiziert.
- Seit **P6-S8**: Anlegen unter bestehendem Namen erzeugt Version 2 (Repository + API), Listen-Endpunkt liefert ohne Filter nur die neueste Version je Name, mit `?name=` die volle Historie absteigend sortiert.
- **64 Tests** (vorher 60, 4 neu: siehe oben).
- **Live-Smoke-Test**: `docker compose build workflow-service` + `up -d`, echte BPMN-Datei über curl hochgeladen, Instanz gestartet, bereite Tasks aufgelistet, Task abgeschlossen, Instanzstatus `"completed"` bestätigt — Testdaten anschließend gelöscht. Seit P6-S2 zusätzlich: BPMN-Datei mit Boundary-Timer + `escalation_email` gestartet, nach kurzem Warten über `notification-service` bestätigt, dass eine Eskalations-Benachrichtigung zugestellt wurde.
- Reine Backend-Session, kein Browser-Test nötig (nicht in der UI-Sessions-Liste von `IMPLEMENTATION_PLAN.md`).

## Offene Punkte

- **Rollenprüfung seit P6-S6 nur für Prozessdefinitionen** (`admin.object_config`) — Instanzstart/Task-Abschluss bleiben bewusst für jeden authentifizierten Principal offen (Nutzerentscheidung, siehe "Autorisierung & Wartungsmodus" oben und [ADR 0024](../adr/0024-not-shutdown-gateway-enforced.md)); `completed_by`/`created_by` sind weiterhin reine, ungeprüfte Strings.
- **Script Tasks führen serverseitig beliebigen, in der BPMN-XML eingebetteten Python-Code aus** (SpiffWorkflows Standard-Scripting-Umgebung) — seit **P6-S6** faktisch durch das `admin.object_config`-Gating am Upload-Endpunkt abgesichert (kein separater Ausführungs-Endpunkt, Skript-Code gelangt ausschließlich über den jetzt gegateten Upload ins System). Vor produktivem Mehrnutzerbetrieb mit BPMN-Import durch nicht vollständig vertrauenswürdige Domain-Admins weiterhin zu revisitieren.
- **SLA-Poll-Präzision an das Poll-Intervall gekoppelt** (Default 30s) — keine Echtzeit-Erkennung einer Eskalation, siehe ADR 0020.
- **Keine verteilte Sperre bei mehreren `workflow-service`-Replikaten** — ein horizontal skaliertes Deployment würde denselben Boundary-Timer mehrfach feuern/publizieren, siehe ADR 0020 "Konsequenzen".
- **Nur `DurationTimerEventDefinition`-basierte Boundary-Timer real getestet** (P6-S2) — `CycleTimerEventDefinition` (wiederkehrende Eskalation) und `TimeDateEventDefinition` (fester Zeitpunkt) werden von SpiffWorkflow nativ unterstützt, sind aber diese Session nicht mit einem eigenen Test abgedeckt.
- **Case-Service-Anbindung seit P6-S3**: `case-service` konsumiert `workflow.instance.completed` und matcht über `business_key` (den es beim Instanzstart auf die eigene Case-ID setzt) — workflow-service selbst musste dafür nicht geändert werden, siehe `docs/services/case-service.md`.
- **Signature Tasks seit P6-S7 umgesetzt, aber ohne Modeler-Unterstützung**: die Extension-Attribute (`taskType`/`requiredLevel`) müssen von Hand im hochgeladenen BPMN-XML gesetzt werden — kein grafischer Palette-Eintrag, siehe "Signature Task" oben.
- **Kein Process Designer** — Prozesse können ausschließlich per BPMN-XML-Upload importiert werden, keine grafische Modellierung im System selbst — **P6-S8** (ehemals P6-S6, eigenständige Frontend-Anwendung mit `bpmn-js`, Lizenz siehe [ADR 0021](../adr/0021-bpmn-io-license-watermark.md)). Muss beim Signature Task ansetzen, sobald ein Palette-Eintrag dafür gewünscht ist.
- **Keine föderierten/installationsübergreifenden Prozessschritte** (7.4) — spätere Phase.
- **Import-Validierung ist rein strukturell** (SpiffWorkflow kann die Datei parsen, Prozess-ID auflösbar) — die in 7.1 beschriebene tiefere Validierung gegen im System verfügbare Objekttypen/Ordnerziele/Instanz-Ziele setzt Task-Typen voraus, die es in diesem Grundgerüst noch nicht gibt (z. B. ein "Dokument anlegen"-Task-Typ).
- **`business_key` ist eine unvalidierte opake Referenz** — kein Abgleich gegen den Document Service, ob eine angegebene `document_id` tatsächlich existiert.
- **Kein Rollback auf eine ältere Version als neue "neueste" Version** — eine alte Version bleibt öffnen-/startbar, ein erneutes "Vorne-Setzen" bräuchte einen manuellen erneuten Upload ihres `bpmn_xml`, keinen eigenen Endpunkt (seit P6-S8, siehe ADR 0027).
- **Keine Race-Condition-Sperre bei der Versionsvergabe** (kein `SELECT ... FOR UPDATE`) — ein echter Parallel-Konflikt zweier gleichzeitiger Speicherversuche unter demselben Namen scheitert unbehandelt an der `(name, version)`-Unique-Constraint (`500`), dieselbe bewusste Vereinfachung wie bei document-services Versionsnummern-Vergabe.
- **Kein Rückwirkungs-Check beim Löschen einer Prozessdefinition auf Task-Ebene** — nur die Existenz irgendeiner Instanz wird geprüft, nicht ob diese noch tatsächlich läuft.
