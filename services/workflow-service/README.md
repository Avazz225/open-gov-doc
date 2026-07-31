# workflow-service

Workflow Engine Grundgerüst (Konzept 7.1): BPMN-2.0-Import und -Ausführung
über [SpiffWorkflow](https://github.com/sartography/SpiffWorkflow) (LGPLv3,
siehe [ADR 0018](../../docs/adr/0018-spiffworkflow-lgpl-license.md)), Manual/
Automatic Tasks. Reines Backend-Grundgerüst - kein UI, keine Rollenprüfung
(erst [P6-S4](../../PROGRESS.md)), kein Process Designer (erst P6-S6).

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/process-definitions` | Anlegen (multipart: `bpmn_xml` Datei, `name`, optional `process_id`) |
| `GET` | `/process-definitions` | Liste (Metadaten) |
| `GET` | `/process-definitions/{id}` | Detail inkl. `bpmn_xml` |
| `DELETE` | `/process-definitions/{id}` | Löschen - `409` falls Instanzen existieren |
| `POST` | `/process-definitions/{id}/instances` | Instanz starten (`created_by`, optional `business_key`/`initial_data`) |
| `GET` | `/instances/{id}` | Status/Metadaten |
| `GET` | `/instances?process_definition_id=&status=&business_key=` | Gefilterte Liste |
| `GET` | `/instances/{id}/tasks` | Bereite Manual/User Tasks |
| `POST` | `/instances/{id}/tasks/{task_id}/complete` | Task abschließen (`completed_by`, optional `data`) |
| `GET` | `/healthz` | Health-Check |

Details/Schema/Events/Offene Punkte: siehe `../../docs/services/workflow-service.md`.

## SpiffWorkflow-Adapter

Die gesamte SpiffWorkflow-API-Oberfläche ist in `src/workflow_service/spiff_adapter.py`
isoliert (Parsing, Ausführung, Serialisierung) - `repository.py` kennt SpiffWorkflow
selbst nicht direkt. Siehe Docstring der Datei für die gegen die installierte Version
(3.1.2) verifizierten Methodennamen.

## State-Persistenz (ADR 0019)

Jede Prozessinstanz speichert den vollständigen, von SpiffWorkflow serialisierten
Ausführungszustand als JSON-Blob - keine eigene, normalisierte Task-Tabelle. Bereite
Tasks werden bei jedem Lesezugriff live aus diesem Blob abgeleitet.

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats registry-service workflow-service
curl localhost:8014/healthz
```

## Tests

```bash
uv run pytest services/workflow-service/tests
```

`test_spiff_adapter.py` testet den Adapter isoliert gegen echte BPMN-Test-Fixtures
(`tests/fixtures/`, aus dem offiziellen SpiffWorkflow-Repo übernommen), unabhängig
von Datenbank/API. `test_repository.py`/`test_api.py` laufen gegen eine echte
Postgres-Instanz (kein Mocking) - wie bei jedem anderen Service niemals gegen die
laufende Entwicklungs-Datenbank, siehe `PROGRESS.md` "Tooling & Testing".
