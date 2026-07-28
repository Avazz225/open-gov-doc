# object-type-service

**Verantwortung:** Objekttyp-Definitionen (Attribute, Pflichtfelder, Namenskonventionen, bedingte Regeln) für Dokumente und Ordner (Konzept 2.2) sowie deren Validierung (4.5, "Constraint Engine").

**Konzept-Referenz:** 2.2, 4.5
**Eigenes Postgres-Schema:** `object_type` (Tabelle `object_type`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/object-types` | Anlegen (`name`, `applies_to`, `attributes`, `naming_constraints`, `conditions`, `allowed_parent_types`, `icon`) — 409 bei doppeltem Namen, 422 bei ungültigem `allowed_parent_types`/`icon` (siehe 2.2a unten) |
| `GET` | `/object-types?applies_to=document\|folder` | Liste, optional gefiltert |
| `GET` | `/object-types/{id}` | Einzelne Definition |
| `PUT` | `/object-types/{id}` | Definition ersetzen (Attribute/Naming/Conditions/`allowed_parent_types`/`icon`) — `name`/`applies_to` bleiben unveränderlich |
| `DELETE` | `/object-types/{id}` | Löschen |
| `POST` | `/object-types/{id}/validate` | `{name, attributes, parent_object_type_id?, parent_is_root?}` → `{valid, errors}` — Platzierungs-Parameter seit P5b-S1 (2.2a) |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

`object_type`: `id`, `name` (unique), `applies_to` (`"document"`\|`"folder"`), `attributes` (JSON-Liste, siehe Schema unten), `naming_constraints` (JSON, nullable), `conditions` (JSON-Liste), `allowed_parent_types` (JSON-Liste von Objekttyp-Namen oder `"$ROOT"`, nullable — 2.2a, seit P5b-S1), `icon` (String, nullable, nur für `applies_to="folder"` — 2.2a, seit P5b-S1), `created_at`/`updated_at`.

## Objekttyp-Schema (2.2)

```json
{
  "attributes": [
    { "name": "Rechnungsnummer", "type": "string", "required": true, "pattern": "RE-\\d{6}" },
    { "name": "Betrag", "type": "decimal", "required": true, "min": 0 }
  ],
  "naming_constraints": {
    "mustContain": ["Rechnungsnummer"],
    "pattern": "{Rechnungsnummer}_{Datum}"
  },
  "conditions": [
    { "if": "Betrag > 10000", "then": "require:Kostenstelle" }
  ]
}
```

Unterstützte Attribut-Typen: `string`, `decimal`, `integer`, `boolean`, `date`, `reference`.

## Erzwungene Objekt-Hierarchie & Icons (2.2a, seit P5b-S1)

Jeder Objekttyp kann über `allowed_parent_types` festlegen, unter welchen Ordnerklassen er platziert werden darf — z. B. `meinTopLevelOrd` nur unter `"$ROOT"` (direkt unter der Wurzel), `meinSecondLevelOrd` nur unter `meinTopLevelOrd`, `meinDoc` nur unter `meinSecondLevelOrd`. Mehrfachangabe (mehrere zulässige Elternklassen) ist erlaubt. Fehlt das Feld oder ist die Liste leer, bleibt der Typ wie bisher überall platzierbar.

- **Validierung bei Anlage/Änderung des Objekttyps** (nicht erst beim Platzierungsversuch): jeder Eintrag muss entweder `"$ROOT"` sein oder eine bereits existierende Ordnerklasse (`applies_to="folder"`) referenzieren — sonst `422`. Nur Ordner können Elternobjekte sein (2.1).
- **`icon`** ist nur für Ordnerklassen zulässig (`422` bei Dokumentklassen) — Anzeige im User-UI-Explorer vor dem Namen folgt erst mit P5b-S4.
- **Durchsetzung**: `POST /object-types/{id}/validate` nimmt `parent_object_type_id`/`parent_is_root` entgegen und löst daraus den Namen der Elternklasse selbst auf (dieser Service bleibt die einzige Quelle der Objekttyp-Namen, siehe **ADR 0013**) — Folder Service und Document Service übertragen nur, was sie ohnehin kennen (die `object_type_id` des Elternordners bzw. dass er die Wurzel ist), kein zusätzlicher Roundtrip nötig.
- **Kein Rückwirkungs-Check**: Verschärft man `allowed_parent_types` nachträglich, werden bereits bestehende Platzierungen nicht rückwirkend geprüft — nur künftige Anlagen/Verschiebungen (Konzept 13, offener Punkt).
- **Keine Zyklen-Erkennung** über mehrere Klassen hinweg (siehe ADR 0013, Konsequenzen).

## Constraint Engine (4.5)

Die eigentliche Validierungslogik liegt in `libs/dms-constraint-engine` (reine, zustandslose Bibliothek) — **siehe ADR 0003** für die Begründung, warum das keine eigene Service ist. Dieser Service ist der einzige Ort, der die Lib importiert; andere Services (Document Service, Folder Service) rufen ausschließlich `/object-types/{id}/validate` über HTTP auf.

Unterstützt (Minimum laut 4.5): Pflichtfelder, bedingte Pflichtfelder, Musterprüfung (Regex) für Werte und Namen, Wertebereiche (`min`/`max`), sowie seit P5b-S1 die Platzierungs-Hierarchie aus 2.2a (`allowedParentTypes`/`parent_type_name`, siehe oben).

**Bewusst vereinfacht**: `type: "reference"` prüft nur das Format (nicht-leerer String), nicht die tatsächliche Existenz des referenzierten Objekts beim zuständigen Service — eine generische "Referenztyp → Service"-Auflösung existiert noch nicht.

**Nicht geprüft**: Ob ein aufrufender Service (Document/Folder Service) tatsächlich einen zu seinem eigenen Typ passenden `applies_to`-Wert verwendet (z. B. ein Dokument mit einem `"folder"`-Objekttyp) — liegt in der Verantwortung des Aufrufers.

## Events

Keine — reiner Referenzdaten-Dienst, wird synchron über HTTP abgefragt, nicht über Events konsumiert/publiziert.

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Tests

- `uv run pytest services/object-type-service/tests`: Repository (CRUD, `allowed_parent_types`/`icon`-Validierung inkl. Ablehnung unbekannter/Nicht-Ordner-Referenzen), API (`/validate` inkl. `parent_is_root`/`parent_object_type_id`-Auflösung, 422 bei ungültigen 2.2a-Feldern).

## Offene Punkte

- Referenz-Existenzprüfung (s. o.) nicht implementiert.
- Statusübergänge (4.5 nennt "bei Erstellung, Änderung und Statusübergängen") werden noch nicht ausgewertet — es gibt noch keinen Workflow-/Status-Mechanismus (folgt Phase 6).
- **Kein GUI-Editor für `allowed_parent_types`/`icon`** — folgt mit P5b-S3 (GUI-Objekttyp-/Layout-Designer); diese Session deckt nur das Backend-Schema/die Durchsetzung ab, verifiziert per curl/pytest.
- Kein Rückwirkungs-Check und keine Zyklen-Erkennung für `allowed_parent_types` (siehe ADR 0013).
