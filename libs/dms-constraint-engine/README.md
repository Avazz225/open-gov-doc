# dms-constraint-engine

Regel-Engine (Konzept 4.5): validiert Attribute/Namen gegen ein Objekttyp-Schema
(2.2). Reine, zustandslose Python-Bibliothek - keine eigene Persistenz, kein
Netzwerkzugriff.

## Warum eine Lib statt eines eigenen Service?

Die Roadmap nennt "Constraint Engine" als eigenständiges Konzept neben dem
Object-Type Service. Die eigentliche Validierungslogik braucht aber keinen
eigenen Prozess/Schema - sie ist eine reine Funktion `schema -> attributes ->
errors`. Persistiert wird sie vom **Object-Type Service**, der diese Lib
nutzt, um seinen `/object-types/{id}/validate`-Endpunkt zu implementieren;
Document Service und Folder Service rufen ausschließlich diesen HTTP-Endpunkt
auf (kein Import fremder Service-Interna), nicht die Lib direkt aus einem
anderen Servicekontext heraus - die Lib selbst ist innerhalb eines einzigen
Service (Object-Type Service) eingebettet.

## API

```python
from dms_constraint_engine import validate

errors: list[str] = validate(schema, name="RE-000123_2026-01-01.pdf", attributes={...})
```

Leere Liste = gültig. Unterstützt (4.5, Minimum):

- Pflichtfelder (`required: true`)
- Bedingte Pflichtfelder (`conditions: [{if: "Betrag > 10000", then: "require:Kostenstelle"}]`,
  restriktiver Vergleichs-Parser - kein `eval()`, nur `attr <op> literal`)
- Musterprüfung (Regex) für Werte (`pattern` je String-Attribut) und für Namen
  (`namingConstraints.pattern`, Platzhalter wie `{Rechnungsnummer}` werden
  durch den tatsächlichen Attributwert ersetzt und gegen den Namen ohne
  Dateiendung geprüft)
- Wertebereiche (`min`/`max` für `decimal`/`integer`)

**Bewusst vereinfacht**: "Verweise auf andere Objekte" (`type: "reference"`)
wird nur auf Format geprüft (nicht-leerer String), nicht auf tatsächliche
Existenz beim referenzierten Service - siehe
`docs/services/object-type-service.md`.

## Tests

```bash
uv run pytest libs/dms-constraint-engine/tests
```

Reine Unit-Tests, keine Infrastruktur nötig.
