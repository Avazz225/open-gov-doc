# folder-service

**Verantwortung:** Ordner als hierarchischer Container (Konzept 2.1) — Anlegen, Umbenennen, Verschieben, Löschen (nur wenn leer), optionale Objekttyp-Validierung. Besitzt die Ordner-Hierarchie und publiziert Struktur-Events, über die der Permission Service seine Rechte-Vererbung synchron hält.

**Konzept-Referenz:** 2.1
**Eigenes Postgres-Schema:** `folder` (Tabelle `folder`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/folders` | Anlegen (`name`, `parent_id` default `"root"`, optional `object_type_id`/`attributes`, `created_by`) |
| `GET` | `/folders/{id}` | Metadaten |
| `GET` | `/folders/{id}/children` | Direkte Unterordner |
| `PATCH` | `/folders/{id}` | Umbenennen und/oder verschieben (`parent_id`) und/oder Attribute ändern |
| `DELETE` | `/folders/{id}` | Löschen — 409, falls noch Unterordner vorhanden |
| `GET` | `/healthz` | Health-Check |

Ein Wurzelordner (`id: "root"`) wird beim Start idempotent angelegt — analog zum `ROOT_RESOURCE_ID` des Permission Service.

## Datenmodell

`folder`: `id`, `name`, `parent_id` (self-FK, nullable nur für `root`), `object_type_id` (opake Referenz auf Object-Type Service, Integer), `attributes` (JSON), `created_by/at`, `updated_at`.

## Objekttyp-Validierung (2.2/4.5)

Trägt ein Folder einen `object_type_id`, wird beim Anlegen `POST /object-types/{id}/validate` des Object-Type Service aufgerufen (Name + Attribute) — schlägt die Validierung fehl, wird der Ordner nicht angelegt (400 mit Fehlerliste). Ohne `object_type_id` entfällt die Prüfung vollständig.

## Struktur-Events (Vertrag mit Permission Service)

Publiziert (Stream `folder`, `ensure_stream=True`) exakt den Vertrag, den der Permission Service seit P2-S2 provisorisch erwartet hatte (`docs/services/permission-service.md`) — **keine Anpassung nötig, live end-to-end verifiziert**:

| event_type | payload |
|---|---|
| `folder.resource.created` | `{resource_id, parent_id, resource_type: "folder"}` |
| `folder.resource.moved` | `{resource_id, new_parent_id}` (nur wenn sich `parent_id` tatsächlich ändert) |
| `folder.resource.deleted` | `{resource_id}` |

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- Löschen ist eine echte (harte) Löschung, kein Soft-Delete — Ordner mit Inhalten werden über die 409-Regel geschützt, es gibt aber keine Aufbewahrung/Papierkorb-Funktion (folgt ggf. Phase 7).
- Kein Endpunkt für Breadcrumb/vollständigen Pfad — nur direkte Kinder abrufbar, für die aktuellen Bedürfnisse ausreichend.
- Bereichssperren (4.7, "ganzer Ordnerbereich für reguläre Nutzer gesperrt") sind nicht Teil dieser Session — gehören konzeptionell eher zum Permission Service und sind für eine spätere Phase vorgesehen.
