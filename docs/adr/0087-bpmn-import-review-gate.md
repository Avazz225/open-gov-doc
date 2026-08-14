# 0087 — workflow-service: BPMN-Import-Review-Gate über den bestehenden Vier-Augen-Mechanismus

**Status:** akzeptiert (Session 4 von 4, letzte Session der Phase 21, siehe `IMPLEMENTATION_PLAN.md`)
**Kontext:** Post-Roadmap Phase 21 Session 4, betrifft `workflow-service`, `process-designer`

## Entscheidung

`POST /process-definitions` (BPMN-Upload) legte bislang **sofort und ungegated** (bis auf die bereits
bestehende `admin.object_config`-Rollenprüfung, P6-S6-Retrofit) eine neue, sofort instanzstartfähige
Prozessdefinition an — ein hochgeladenes BPMN-Dokument kann Script-Tasks/Connector-Aufrufe enthalten
("ein reales Sicherheitsthema", `docs/services/workflow-service.md`), ein einzelner Admin konnte das
also unbeobachtet aktivieren. Diese Session repliziert exakt das bereits bestehende, generische
Vier-Augen-Rezept aus `config-service`s P17-S3-Retrofit ([ADR 0060](0060-egov-paket-teil-2-vier-augen-luecken-und-umlaufmappen-prozessvorlagen.md)):
ein neuer Aktionstyp `workflow.process_definition.import`, geprüft über `permission-service`s bereits
bestehenden generischen Approval-Mechanismus ([ADR 0022](0022-four-eyes-approval-via-events.md)).

1. **Neuer `workflow_service.approval_client.ApprovalClient`** — identisches Muster wie
   `config_service`s/`document_service`s gleichnamige Klasse, reiner `httpx`-Client gegen
   `GET /approval-config/{action_type}` und `POST /approval-requests`.
2. **`POST /process-definitions` fragt vor der Anlage ab**, ob `workflow.process_definition.import`
   gerade Genehmigung erfordert. Falls ja: BPMN-Text + Name + optionale `process_id` werden als
   `payload` in einen neuen Freigabe-Request gepackt, `202` mit `{status: "pending_approval",
   approval_request_id}` zurückgegeben — **keine** Anlage, keine BPMN-Validierung an dieser Stelle
   (bewusst zurückgestellt, siehe unten).
3. **Neuer, reiner Konsument** (`consumer.py`, `ensure_stream=False`) — reagiert auf
   permission-services bereits bestehendes `permission.approval.approved`-Event, filtert auf
   `action_type == "workflow.process_definition.import"`, wendet den zurückgestellten Import dann über
   dieselbe `repository.create_process_definition` an, die auch der sofortige Pfad nutzt (inkl. der
   dort bereits vorhandenen BPMN-Validierung).
4. **`process-designer`** (`apps/process-designer`) erkennt den `202`-Fall und zeigt einen Hinweis statt
   zur (noch nicht existierenden) neuen Definition zu navigieren.

## Begründung

- **Warum der Erfolgsfall (keine Genehmigungspflicht konfiguriert) NICHT wie bei `config-service` in
  eine Status-Hülle verpackt wird**: `config-service`s `ImportActionResult` verpackt JEDE Antwort
  (`applied`/`pending_approval`) einheitlich — dort vertretbar, da `POST /config/import` ein
  vergleichsweise selten in Tests verwendeter Endpunkt ist. `POST /process-definitions` ist dagegen in
  `workflow-service`s eigenem Testbestand tief verwurzelte Test-**Infrastruktur**: über 40 Aufrufstellen
  in `test_api.py` (und weitere in `test_federation.py`) laden zunächst eine Prozessdefinition hoch,
  BEVOR der eigentlich zu testende Sachverhalt beginnt (Instanzstart, Task-Abschluss, DMN-Auswertung,
  Föderation, ...) — sie interessieren sich nicht für diese Session, sondern brauchen nur eine fertige
  Definition. Eine einheitliche Hülle hätte alle diese Aufrufstellen (und `process-designer`s
  bestehenden, unveränderten Erfolgspfad) angefasst, ohne dass diese Tests inhaltlich etwas mit
  Vier-Augen zu tun haben. Stattdessen: der Erfolgsfall bleibt **byteidentisch** zum bisherigen
  Verhalten (`201` + `ProcessDefinitionOut`), nur der neue, bislang nicht existierende
  `pending_approval`-Fall bekommt eine eigene Form (`202` + `ProcessDefinitionImportResult`) —
  unterscheidbar bereits am HTTP-Status, kein Client muss die Erfolgsform umstellen.
- **Warum BPMN-Validierung erst beim Konsumieren des genehmigten Imports passiert, nicht schon beim
  Anlegen des Freigabe-Requests**: identische Begründung wie `config_service._apply_config_document`,
  das seine Schema-Validierung ebenfalls erst dort vornimmt — der Freigabe-Request soll die Anfrage so
  transportieren, wie sie eingereicht wurde; eine vorzeitige Ablehnung wegen ungültigem BPMN wäre zwar
  früheres Feedback für den Einreichenden, würde aber die Zurückstellungs-Semantik verkomplizieren
  (zwei verschiedene Fehlerpfade statt eines). Ein ungültiges BPMN scheitert stattdessen beim
  Konsumieren, geloggt statt einem HTTP-Aufrufer gemeldet (kein Aufrufer mehr vorhanden) — exakt
  `config_service.consumer`s bereits etabliertes, breites Exception-Handling.
- **Warum `create_process_definition`s License-Gate (`_license_gate("write")`) UNVERÄNDERT vor der
  neuen Genehmigungsprüfung bleibt**: Lizenzstatus ist eine Deployment-/Vertragsfrage, unabhängig vom
  Vier-Augen-Prinzip — ein unlizenzierter/Demo-Modus-Aufruf soll gar nicht erst einen Freigabe-Request
  erzeugen können.

## Konsequenzen

- **Migration**: keine DB-Änderung nötig (kein neues Feld auf `ProcessDefinition` — der Freigabe-Request
  selbst, nicht eine DB-Zeile, ist der "wartende" Zustand, identisches Muster wie `config-service`, wo
  ebenfalls keine Zeile vor der Genehmigung existiert).
- **`scripts/run-tests.sh`**: `workflow-service` zur `CONSUMER_SERVICES`-Liste hinzugefügt (Services mit
  eigenem NATS-Konsumenten, deren Tests eigenständig per In-Prozess-`TestClient` laufen) — vorher hatte
  `workflow-service` gar keinen eigenen Konsumenten (nur einen Producer für seinen `"workflow"`-Stream),
  seit dieser Session braucht sein Testlauf denselben Container-Stopp-Schutz wie `document-service`/
  `notification-service`/etc., sonst konkurrieren der laufende Container-Konsument und der
  In-Prozess-Test-Konsument um denselben Durable-Namen.
- **Echte, während dieser Session gefundene Regression aus einer früheren Session behoben**: die volle
  `workflow-service`-Testsuite (zum ersten Mal seit Post-Roadmap Phase 20 Session 5 komplett gelaufen)
  deckte auf, dass `test_dispatch_records_delivery_failed_for_unreachable_target`
  (`test_federation.py`) noch die VOR ADR 0081 gültige Statuserwartung (`"delivery_failed"` sofort bei
  einem einzelnen Fehlschlag) hatte — seit ADR 0081 markiert der Hub einen einzelnen Fehlschlag als
  retry-fähiges `"pending_retry"`, erst nach Erschöpfung `max_handover_delivery_attempts` als
  `"delivery_failed"`. `workflow-service`s `federation_task.status` übernimmt `handover["status"]`
  unverändert (`repository.update_federation_task_status`), war also technisch bereits korrekt — nur
  der Test war seit P20-S5 stillschweigend veraltet, unbemerkt, weil `workflow-service`s Tests seither
  nie in vollem Umfang liefen. Behoben durch Anpassung der Testerwartung auf `"pending_retry"`.
- **Tests**: `workflow-service` 177 (vorher 170, +7: neue `test_consumer.py` mit 5 Tests, ein neuer
  API-Integrationstest gegen den echten `permission-service`, sowie die oben beschriebene
  Regressionskorrektur). `process-designer`: bestehende 39 Tests unverändert grün (kein dedizierter
  neuer Test für den `pending_approval`-Zweig der `designer/page.tsx`-Speicherfunktion — dieser
  Save-Flow hatte bereits vor dieser Session keine Testabdeckung; `tsc`/`eslint`/`next build` bestätigen
  Typkorrektheit, kein echter Browser in dieser Entwicklungsumgebung verfügbar für eine visuelle
  Prüfung, siehe `docs/services/admin-ui.md` "Kein Browser..." für dieselbe projektweite Einschränkung).
- **Live gegen den echten laufenden Stack verifiziert** (Image-Neubau + Neustart von
  `workflow-service`): ein Test-Principal erhielt live die Rolle `domain-admin-config`, Genehmigungspflicht
  für `workflow.process_definition.import` aktiviert, ein echtes BPMN hochgeladen — lieferte `202` +
  `pending_approval`, die Prozessfamilie existierte danach nachweislich noch nicht
  (`GET /process-definitions?name=...` leer); nach echtem `POST .../approve` gegen den laufenden
  `permission-service` wurde die Definition innerhalb weniger Sekunden vom neuen Konsumenten tatsächlich
  angelegt (`bpmn_process_id` korrekt aus dem BPMN extrahiert) — bestätigt den vollständigen
  Anfrage→Genehmigung→Konsum-Kreislauf gegen echte, unabhängig laufende Container.
- Doku: neues [ADR 0087](0087-bpmn-import-review-gate.md), `docs/services/workflow-service.md`
  (API-Tabelle, neue Sektion, "Offene Punkte" als behoben markiert), `docs/services/process-designer.md`
  (neues Verhalten beim Speichern) ergänzt.
