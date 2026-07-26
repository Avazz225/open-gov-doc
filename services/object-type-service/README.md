# object-type-service

Objekttyp-Definitionen (Attribute, Pflichtfelder, Namenskonventionen, bedingte
Regeln) für Dokumente und Ordner (Konzept 2.2) + Validierungs-Endpunkt
("Constraint Engine", 4.5).

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/object-types` | Anlegen |
| `GET` | `/object-types?applies_to=document\|folder` | Liste |
| `GET` | `/object-types/{id}` | Einzelne Definition |
| `PUT` | `/object-types/{id}` | Aktualisieren |
| `DELETE` | `/object-types/{id}` | Löschen |
| `POST` | `/object-types/{id}/validate` | `{name, attributes}` gegen die Definition prüfen |
| `GET` | `/healthz` | Health-Check |

Details/Schema-Format: siehe `../../docs/services/object-type-service.md`.

## Constraint Engine als Lib, nicht als eigener Service

Die Validierungslogik lebt in `libs/dms-constraint-engine` (reine, zustandslose
Funktion). Dieser Service ist der einzige, der sie importiert - andere
Services rufen ausschließlich `/object-types/{id}/validate` über HTTP auf.
Begründung: `../../docs/adr/0003-constraint-engine-as-library.md`.

## Registry-Registrierung (seit P4-S1)

Meldet sich beim Start über `dms-registry-client` selbst bei der Registry an (Heartbeat, Deregister beim Shutdown) - Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`, siehe `docs/services/gateway-service.md` für den Konsumenten (API-Gateway, dynamisches Routing).

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres object-type-service
curl localhost:8007/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres && cd ..
uv run pytest services/object-type-service/tests
```
