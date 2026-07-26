# object-type-service

**Verantwortung:** Objekttyp-Definitionen (Attribute, Pflichtfelder, Namenskonventionen, bedingte Regeln) für Dokumente und Ordner (Konzept 2.2) sowie deren Validierung (4.5, "Constraint Engine").

**Konzept-Referenz:** 2.2, 4.5
**Eigenes Postgres-Schema:** `object_type` (Tabelle `object_type`)

## API

| Methode | Pfad | Beschreibung |
|---|---|---|
| `POST` | `/object-types` | Anlegen (`name`, `applies_to`, `attributes`, `naming_constraints`, `conditions`) — 409 bei doppeltem Namen |
| `GET` | `/object-types?applies_to=document\|folder` | Liste, optional gefiltert |
| `GET` | `/object-types/{id}` | Einzelne Definition |
| `PUT` | `/object-types/{id}` | Definition ersetzen (Attribute/Naming/Conditions) |
| `DELETE` | `/object-types/{id}` | Löschen |
| `POST` | `/object-types/{id}/validate` | `{name, attributes}` → `{valid, errors}` |
| `GET` | `/healthz` | Health-Check |

## Datenmodell

`object_type`: `id`, `name` (unique), `applies_to` (`"document"`\|`"folder"`), `attributes` (JSON-Liste, siehe Schema unten), `naming_constraints` (JSON, nullable), `conditions` (JSON-Liste), `created_at`/`updated_at`.

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

## Constraint Engine (4.5)

Die eigentliche Validierungslogik liegt in `libs/dms-constraint-engine` (reine, zustandslose Bibliothek) — **siehe ADR 0003** für die Begründung, warum das keine eigene Service ist. Dieser Service ist der einzige Ort, der die Lib importiert; andere Services (Document Service, Folder Service) rufen ausschließlich `/object-types/{id}/validate` über HTTP auf.

Unterstützt (Minimum laut 4.5): Pflichtfelder, bedingte Pflichtfelder, Musterprüfung (Regex) für Werte und Namen, Wertebereiche (`min`/`max`).

**Bewusst vereinfacht**: `type: "reference"` prüft nur das Format (nicht-leerer String), nicht die tatsächliche Existenz des referenzierten Objekts beim zuständigen Service — eine generische "Referenztyp → Service"-Auflösung existiert noch nicht.

**Nicht geprüft**: Ob ein aufrufender Service (Document/Folder Service) tatsächlich einen zu seinem eigenen Typ passenden `applies_to`-Wert verwendet (z. B. ein Dokument mit einem `"folder"`-Objekttyp) — liegt in der Verantwortung des Aufrufers.

## Events

Keine — reiner Referenzdaten-Dienst, wird synchron über HTTP abgefragt, nicht über Events konsumiert/publiziert.

## Selbst-Registrierung (Konzept 3.2a, seit P4-S1)

Registriert sich beim Start selbst bei der Registry (`libs/dms-registry-client`: Register, periodischer Heartbeat, Deregister beim Shutdown) - Grundlage für das Routing des API-Gateways (`docs/services/gateway-service.md`). Opt-in über `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; ohne beide Werte läuft der Service unverändert ohne Discovery.

## Sensoren (Konzept 10.1)

Noch keine — folgt in Phase 11.

## Offene Punkte

- Referenz-Existenzprüfung (s. o.) nicht implementiert.
- Kein Admin-UI-Editor für Objekttypen (folgt P4-S3).
- Statusübergänge (4.5 nennt "bei Erstellung, Änderung und Statusübergängen") werden noch nicht ausgewertet — es gibt noch keinen Workflow-/Status-Mechanismus (folgt Phase 6).
