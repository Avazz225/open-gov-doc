# case-service

Umlaufmappen (Konzept 2.3): buendeln Referenzen (keine eigenen Kopien) auf
Dokumente, die zu einem Vorgang gehoeren - der Lebenszyklus laeuft ueber eine
Prozessinstanz in [workflow-service](../workflow-service/) (7.1, P6-S1).
Waehrend die Umlaufmappe offen ist, wird je referenziertem Dokument dynamisch
die aktuellste Version aufgeloest; beim Erreichen des BPMN-Endzustands wird
die Referenzstruktur als **Abschluss-Snapshot** fixiert.

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/cases` | Anlegen (`name`, optional `object_type_id`/`attributes`, `process_definition_id`, `created_by`, optional `initial_data`) - startet dabei eine Prozessinstanz in workflow-service |
| `GET` | `/cases` | Liste, Filter `status`/`object_type_id` |
| `GET` | `/cases/{id}` | Detail |
| `POST` | `/cases/{id}/documents` | Dokumentreferenz hinzufuegen (`document_id`, `added_by`) |
| `DELETE` | `/cases/{id}/documents/{document_id}` | Referenz entfernen (`removed_by`, weicher Delete) |
| `GET` | `/cases/{id}/documents` | Referenzen inkl. aufgeloester Version (dynamisch offen / fixiert geschlossen) |
| `GET` | `/healthz` | Health-Check |

Details/Schema/Events/Offene Punkte: siehe `../../docs/services/case-service.md`.

## Abschluss-Snapshot

case-service ist der erste Konsument von workflow-services
`workflow.instance.completed`-Event (siehe `consumer.py`). Da die
Prozessinstanz beim Start mit `business_key = case_id` gestartet wird, kann
der Handler die zugehoerige Umlaufmappe direkt ueber diesen Wert finden und
fuer jede aktive Dokumentreferenz die dann aktuelle Version fixieren.

## Lokale Ausfuehrung

```bash
cd infra && docker compose up -d postgres nats registry-service workflow-service document-service object-type-service case-service
curl localhost:8016/healthz
```

## Tests

```bash
uv run pytest services/case-service/tests
```

Laeuft wie bei jedem anderen Service gegen eine echte Postgres-Instanz statt
gegen Mocks. API-Tests, die `object_type_id` bzw. einen echten Workflow-Start
nutzen, brauchen zusaetzlich lokal erreichbare `object-type-service`-/
`workflow-service`-/`document-service`-Instanzen (gleiches Muster wie
document-services `folder_client`/`object_type_client`-Tests).
