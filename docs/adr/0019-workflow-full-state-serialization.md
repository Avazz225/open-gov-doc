# 0019 — Workflow-Instanzstatus als voller serialisierter Blob statt normalisierter Task-Tabelle

**Status:** akzeptiert
**Kontext:** P6-S1 (Workflow Engine Grundgerüst, Konzept 7.1). `workflow-service` muss den Ausführungszustand jeder Prozessinstanz zwischen einzelnen, zustandslosen HTTP-Requests persistieren (Postgres, `dms-db-base`), da SpiffWorkflow selbst keine eigene Persistenzschicht mitbringt.

## Entscheidung

Jede `process_instance`-Zeile speichert den vollständigen, von `SpiffWorkflow.bpmn.serializer.BpmnWorkflowSerializer.serialize_json()` erzeugten JSON-Blob in einer einzelnen `Text`-Spalte (`workflow_state`). Es gibt **keine** separate, normalisierte Tabelle für einzelne Tasks/Schritte. Bereite Manual/User Tasks werden bei jedem Lesezugriff live durch Deserialisieren des Blobs ermittelt (`spiff_adapter.ready_manual_tasks()`), nicht aus einer eigenen Projektion gelesen.

## Begründung

- **SpiffWorkflow besitzt bereits die korrekte BPMN-Ausführungssemantik** (Parallel-/Exklusiv-/Inklusiv-Gateways, Schleifen, Sub-Prozesse, Boundary Events in späteren Sessions) — eine eigene, normalisierte Task-Tabelle würde einen Teil dieses Zustands duplizieren und könnte bei jeder neuen unterstützten BPMN-Konstruktion (z. B. Multi-Instance-Tasks) erneut nachgezogen werden müssen, mit dem Risiko, dass beide Repräsentationen auseinanderlaufen.
- **Serialisierung/Deserialisierung ist der von SpiffWorkflow selbst dokumentierte Weg**, eine Workflow-Instanz über mehrere zustandslose Aufrufe hinweg fortzusetzen (siehe `spiff_adapter.py`-Docstring) — dagegen zu arbeiten statt damit hieße, eine parallele Zustandsverwaltung zu bauen, die SpiffWorkflow bereits selbst korrekt löst.
- **Task-IDs bleiben über Serialisierung/Deserialisierung hinweg stabil** (empirisch verifiziert) — die für die API nötige Adressierbarkeit einzelner Tasks (`GET .../tasks`, `POST .../tasks/{id}/complete`) ist damit ohne eigene ID-Verwaltung gegeben.
- **Kein aktueller Bedarf für eine eigene Projektion**: In diesem Grundgerüst gibt es keine Cross-Instanz-Abfrage wie "alle bereiten Tasks über alle laufenden Instanzen hinweg" (ein Task-Inbox-UI existiert erst ab P6-S8/später) — eine Deserialisierung je Instanz bei Bedarf ist für die aktuelle Größenordnung ausreichend performant.

## Konsequenzen

- Eine Abfrage "alle bereiten Tasks über alle laufenden Instanzen" (z. B. für ein künftiges Task-Inbox-UI) erfordert das Deserialisieren jeder laufenden Instanz — kein SQL-seitiger Filter auf Task-Ebene möglich. Bei absehbarem Bedarf einer effizienten Cross-Instanz-Abfrage (vermutlich im Umfeld von P6-S8) wäre eine zusätzliche, aus dem Blob abgeleitete Projektionstabelle nachzuziehen (reine Lesebeschleunigung, keine Ersetzung des Blobs als Quelle der Wahrheit).
- Der Blob ist für `workflow-service` selbst weitgehend intransparent — jede Interpretation läuft ausschließlich über `spiff_adapter.py`, nie direkt über SQL/JSON-Operatoren auf der Spalte. Ein künftiger SpiffWorkflow-Versions-Bump mit geändertem Serialisierungsformat betrifft nur dieses eine Modul, erfordert aber ggf. eine Migrationsstrategie für bereits gespeicherte, ältere Blobs (in dieser Session nicht relevant, da noch keine Produktivdaten existieren).
- Kein Audit-Trail auf Feld-Ebene innerhalb eines Tasks (z. B. "welcher Wert wurde für welches Formularfeld eingetragen") jenseits dessen, was im `workflow.task.completed`-Event landet — für eine detaillierte Nachvollziehbarkeit wäre das Event-Payload bei Bedarf zu erweitern, nicht die Persistenzstruktur selbst.
