# folder-service

Ordner-Hierarchie (Konzept 2.1): Anlegen, Umbenennen, Verschieben, Löschen
(nur wenn leer). Publiziert Struktur-Events, über die der Permission Service
seine Rechte-Vererbung synchron hält.

## Endpunkte

| Methode | Pfad | Zweck |
|---|---|---|
| `POST` | `/folders` | Anlegen (`name`, `parent_id` default `"root"`, `created_by`, optional `object_type_id`/`attributes`) |
| `GET` | `/folders/{id}` | Metadaten |
| `GET` | `/folders/{id}/children` | Direkte Unterordner |
| `PATCH` | `/folders/{id}` | Umbenennen/Verschieben/Attribute ändern |
| `DELETE` | `/folders/{id}` | Löschen (409, falls nicht leer) |
| `GET` | `/healthz` | Health-Check |

Details/Events: siehe `../../docs/services/folder-service.md`.

## Struktur-Vertrag mit dem Permission Service

Dieser Service implementiert exakt den Vertrag, den `permission-service` seit
P2-S2 provisorisch erwartet hat (`folder.resource.created/.moved/.deleted`) -
keine Anpassung war nötig. In P3-S3 live end-to-end verifiziert: ein über
diese API angelegter Ordner erscheint unmittelbar im `resource_node`-Baum des
Permission Service.

## Objekttyp-Validierung

Trägt ein Ordner einen `object_type_id`, validiert dieser Service die
Attribute vor dem Anlegen gegen den Object-Type Service (`POST
/object-types/{id}/validate`) - ohne `object_type_id` entfällt die Prüfung.

## Registry-Registrierung (seit P4-S1)

Meldet sich beim Start über `dms-registry-client` selbst bei der Registry an (Heartbeat, Deregister beim Shutdown) - Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`, siehe `docs/services/gateway-service.md` für den Konsumenten (API-Gateway, dynamisches Routing).

## Lokale Ausführung

```bash
cd infra && docker compose up -d postgres nats object-type-service folder-service
curl localhost:8008/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats object-type-service && cd ..
uv run pytest services/folder-service/tests
```

`test_object_type_validation.py` braucht einen laufenden Object-Type Service.
