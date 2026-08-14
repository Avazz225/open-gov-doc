# dms-constraint-engine

Rule engine (Concept 4.5): validates attributes/names against an object-type schema
(2.2). Pure, stateless Python library - no persistence of its own, no
network access.

## Why a lib instead of its own service?

The roadmap names "Constraint Engine" as a standalone concept alongside the
Object-Type Service. The actual validation logic, however, does not need its
own process/schema - it is a pure function `schema -> attributes ->
errors`. It is persisted by the **Object-Type Service**, which uses this lib
to implement its `/object-types/{id}/validate` endpoint;
Document Service and Folder Service exclusively call this HTTP endpoint
(no importing of foreign service internals), not the lib directly from
another service context - the lib itself is embedded within a single
service (Object-Type Service).

## API

```python
from dms_constraint_engine import validate

errors: list[str] = validate(schema, name="RE-000123_2026-01-01.pdf", attributes={...})
```

Empty list = valid. Supports (4.5, minimum):

- Required fields (`required: true`)
- Conditional required fields (`conditions: [{if: "Betrag > 10000", then: "require:Kostenstelle"}]`,
  restrictive comparison parser - no `eval()`, only `attr <op> literal`)
- Pattern checking (regex) for values (`pattern` per string attribute) and for names
  (`namingConstraints.pattern`, placeholders like `{Rechnungsnummer}` are
  replaced with the actual attribute value and checked against the name without
  file extension)
- Value ranges (`min`/`max` for `decimal`/`integer`)

**Deliberately simplified**: "references to other objects" (`type: "reference"`)
is only checked for format (non-empty string), not for actual
existence at the referenced service - see
`docs/services/object-type-service.md`.

## Tests

```bash
uv run pytest libs/dms-constraint-engine/tests
```

Pure unit tests, no infrastructure needed.
