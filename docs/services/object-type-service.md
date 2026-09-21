# object-type-service

**Responsibility:** Object type definitions (attributes, required fields, naming conventions, conditional rules) for documents and folders (Concept 2.2), as well as their validation (4.5, "Constraint Engine"). Since **P6-S7**, every document class additionally carries an optional minimum signature level (3.10), enforced by `signature-service`, not here.

**Concept reference:** 2.2, 4.5, 3.10
**Own Postgres schema:** `object_type` (table `object_type`)

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/object-types` | Create (`name`, `applies_to`, `attributes`, `naming_constraints`, `conditions`, `allowed_parent_types`, `status_transitions`, `icon`, `required_signature_level`, `default_retention_days`, `deletion_reason_required_override`, `default_archive_after_days`, `archive_encryption_enabled`, `classification_level`) — 409 on duplicate name, 422 on invalid `allowed_parent_types`/`status_transitions`/`icon`/`required_signature_level`/`default_retention_days`/`default_archive_after_days`/`classification_level` (see 2.2a, "Status-Transition Rules (4.5/7.1)" below, or "Minimum Signature Level"/"Retention"/"Records Disposal"/"Classified Document Classification") |
| `GET` | `/object-types?applies_to=document\|folder` | List, optionally filtered |
| `GET` | `/object-types/{id}` | Single definition |
| `PUT` | `/object-types/{id}` | Replace definition (attributes/naming/conditions/`allowed_parent_types`/`status_transitions`/`icon`/`required_signature_level`/`default_retention_days`/`deletion_reason_required_override`/`default_archive_after_days`/`archive_encryption_enabled`/`classification_level`) — `name`/`applies_to` remain immutable |
| `DELETE` | `/object-types/{id}` | Delete |
| `POST` | `/object-types/{id}/validate` | `{name, attributes, parent_object_type_id?, parent_is_root?, from_status?, to_status?}` → `{valid, errors}` — placement parameters since P5b-S1 (2.2a); `from_status`/`to_status` since **Phase 45 Session 4** (4.5/7.1, both unset = not a status transition, unchanged behavior) |
| `GET` | `/object-types/{id}/layouts/{purpose}` | Form layout (`purpose`: `display`\|`search`\|`upload`, 2.2b, since P5b-S2) — returns a saved override (`is_custom: true`) or a smart layout generated from the current attributes (`is_custom: false`), 404 on unknown `object_type_id` |
| `PUT` | `/object-types/{id}/layouts/{purpose}` | Saves an explicit layout override — 422 on reference to an unknown attribute, 404 on unknown `object_type_id` |
| `DELETE` | `/object-types/{id}/layouts/{purpose}` | Removes an override (resets to the generated smart layout) — idempotent, 404 only on unknown `object_type_id` |
| `POST` | `/object-types/{id}/next-kennzeichen` | Reference number generator (2.2, since P5e-S1): atomic increment+format call, returns `{kennzeichen: "2026-001"}` — 404 on unknown `object_type_id` or if no `kennzeichen_format` is configured. **Since P17-S2** optional body `{attributes: {...}}` for attribute-based placeholders (14.2, e.g. `{Federführung}`) — 422 if a referenced placeholder does not receive a value |
| `GET` | `/kennzeichen-config` | Read global display default (`show_before_filename`, since P5e-S3) |
| `PUT` | `/kennzeichen-config` | Change global display default — takes effect immediately for all document types without their own `kennzeichen_display_override` |
| `GET` | `/healthz` | Health check |

## Data Model

`object_type`: `id`, `name` (unique), `applies_to` (`"document"`\|`"folder"`), `attributes` (JSON list, see schema below), `naming_constraints` (JSON, nullable), `conditions` (JSON list), `allowed_parent_types` (JSON list of object type names or `"$ROOT"`, nullable — 2.2a, since P5b-S1), `status_transitions` (JSON list, `[{"from": str, "to": str, "requiredAttributes": [str]}]`, default `[]` — 4.5/7.1, since Phase 45 Session 4, see "Status-Transition Rules" below), `icon` (string, nullable, only for `applies_to="folder"` — 2.2a, since P5b-S1), `kennzeichen_format` (string, nullable, only for `applies_to="document"` — 2.2, since P5e-S1), `kennzeichen_display_override` (boolean, nullable, tri-state, only for `applies_to="document"` — 2.2, since P5e-S1), `required_signature_level` (string `"ses"`\|`"aes"`\|`"qes"`, nullable, only for `applies_to="document"` — 3.10, since P6-S7), `default_retention_days` (integer, nullable, `>= 0`, for **both** `applies_to` values — 5.2, since P7-S1), `deletion_reason_required_override` (boolean, nullable, tri-state like `kennzeichen_display_override`, for both `applies_to` values — 5.2a, since P7-S1), `default_archive_after_days` (integer, nullable, `>= 0`, for both `applies_to` values — 5.6, since P7-S3), `archive_encryption_enabled` (boolean, default `false`, for both `applies_to` values — 5.6, since P7-S3), `classification_level` (string, nullable, only for `applies_to="document"` — 2.5, since P15-S1, multi-level since P17-S2/14.2, one of the four VS classification levels or `null`), `created_at`/`updated_at`.

`object_type_layout` (2.2b, since P5b-S2): `object_type_id` + `purpose` (`"display"`\|`"search"`\|`"upload"`) as composite primary key (foreign key to `object_type.id`, `ON DELETE CASCADE`), `layout` (JSON: `{rows: [{columns: [{attribute, label, required}]}], responsive_breakpoint_px}`), `created_at`/`updated_at`. Only explicit deviations from the generated smart layout are stored here (see ADR 0014) — if a row is missing, the current generated state applies automatically.

`object_type_sequence` (2.2, since P5e-S1): `object_type_id` + `jahr` as composite primary key (foreign key to `object_type.id`, `ON DELETE CASCADE`), `naechste_nummer` (integer). One row per object type and year — see "Reference Number Generator" below.

`kennzeichen_config` (2.2, since P5e-S3): single row (`id=1`, same pattern as `OcrConfig`/`UploadConfig`) — `show_before_filename` (boolean, default `true`), `updated_at`. No FK relationship to `object_type`, so it must be cleared separately from the `object_type` CASCADE truncate in tests.

## Authorization (Post-Roadmap Phase 38 Session 3)

`POST`/`PUT`/`DELETE /object-types`, `PUT`/`DELETE .../layouts/{purpose}`, and `PUT /kennzeichen-config` previously had NO permission check at all — this service had no `permission_client` of any kind before this session. All now require `X-DMS-Principal` + the EXISTING capability `admin.object_config` (role `domain-admin-config`) — reused rather than a new capability, since this same capability already governed `workflow-service`'s BPMN/DMN process definitions since P6-S6, and its own seeded role description already named "object type" configuration explicitly; it was simply never wired up here. `POST .../validate` and `POST .../next-kennzeichen` remain deliberately ungated — both are called during regular document/folder creation by any authenticated user, not an administrative action. `GET` endpoints (list/get object type, get kennzeichen-config, get layout) also remain ungated — broad read access is needed for creation forms/the layout designer to render at all. See [ADR 0148](../adr/0148-admin-ui-authorization-full-alignment.md).

Known production callers of the now-gated endpoints and how they authenticate: `config-service`'s `ObjectTypeServiceClient` (config import/export, 7.3) and `workflow-service`-adjacent `query-service`'s `ObjectTypeClient` (the `object_type.update` manipulation action, 6.1) both assert a fixed service identity (`X-DMS-Principal: config-service`/`query-service` respectively), each granted `admin.object_config` via a one-time role assignment (not self-seeding — an already-running installation needs this granted once, same "not self-healing" caveat as `permission-service`'s `EVERYONE_ROLE_PERMISSIONS`).

## Object Type Schema (2.2)

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

Supported attribute types: `string`, `decimal`, `integer`, `boolean`, `date`, `reference`.

**`personal_data`** (boolean, optional, 5.2, Post-Roadmap Phase 41 Session 2, [ADR 0156](../adr/0156-attribute-pseudonymization-reversible-vault.md)): a free-form key inside the attribute definition, not a schema field this service itself validates or acts on (`dms_constraint_engine` ignores unrecognized keys) — marks an attribute as eligible for `document-service`'s attribute-level pseudonymization vault (`POST /documents/{id}/attributes/{name}/pseudonymize`). `admin-ui`'s `ObjectTypeEditor` exposes it as a per-attribute checkbox so it's reachable without raw API calls.

## Enforced Object Hierarchy & Icons (2.2a, since P5b-S1)

Each object type can use `allowed_parent_types` to define under which folder classes it may be placed — e.g. `meinTopLevelOrd` only under `"$ROOT"` (directly under the root), `meinSecondLevelOrd` only under `meinTopLevelOrd`, `meinDoc` only under `meinSecondLevelOrd`. Multiple entries (several allowed parent classes) are permitted. If the field is missing or the list is empty, the type remains placeable everywhere, as before.

- **Validation at object type creation/modification** (not only at the moment of a placement attempt): each entry must either be `"$ROOT"` or reference an already existing folder class (`applies_to="folder"`) — otherwise `422`. Only folders can be parent objects (2.1).
- **`icon`** is only allowed for folder classes (`422` for document classes) — display in the User UI explorer before the name follows only with P5b-S4.
- **Enforcement**: `POST /object-types/{id}/validate` accepts `parent_object_type_id`/`parent_is_root` and resolves the parent class's name from that itself (this service remains the single source of object type names, see **ADR 0013**) — Folder Service and Document Service only pass along what they already know (the parent folder's `object_type_id`, or that it is the root), no additional roundtrip needed.
- **No retroactive check**: If `allowed_parent_types` is tightened afterward, already existing placements are not checked retroactively — only future creations/moves (Concept 13, open point).
- **No cycle detection** across multiple classes (see ADR 0013, Consequences).

## Reference Number Generator (2.2, since P5e-S1)

Each document class (`applies_to="document"`) can have a format string field `kennzeichen_format`, which is rendered into a finished file reference number on every `POST .../next-kennzeichen` call (e.g. `{YYYY}-{Laufende_Nummer}` → `2026-001`). `null` means: no generator configured.

- **Supported placeholders**: `{YYYY}` (four-digit year), `{YY}` (two-digit), `{MM}`/`{DD}` (two-digit), `{Laufende_Nummer}` (three-digit zero-padded, e.g. `001`, simply grows in digit count beyond that). `kennzeichen_format` must contain `{Laufende_Nummer}`. **Since P17-S2** (14.2): any additional placeholder that is not one of the date/counter placeholders above is interpreted as the name of an attribute defined on the object type (e.g. `{Federführung}`) — it must actually exist as an attribute name when the format is saved, otherwise `422` (analogous to the `allowedParentTypes` reference check, 2.2a). `POST .../next-kennzeichen` then expects the attribute values in the optional body `{attributes: {...}}` (sent by `document-service` when creating a document) — if a referenced value is still missing (attribute not marked as required), the endpoint returns `422` instead of generating a reference number with a silently empty gap. Direct implementation of the concept example `{Abteilung}-{YYYY}-{Laufende_Nummer}` (14.2), see [ADR 0059](../adr/0059-egov-paket-aktenplan-hierarchie-und-mehrstufige-vs-einstufung.md) for the rationale behind why the eGov package specifically uses `{Federführung}` instead of `{Abteilung}`.
- **`kennzeichen_format`/`kennzeichen_display_override`** are only allowed for `applies_to="document"` — `422` for folder classes. `kennzeichen_display_override` is a tri-state (`null`/`true`/`false`) that, when set, overrides the global "display reference number before file name" default.
- **Counter reset per year**: the running number automatically resets on January 1st, independently per object type (`object_type_sequence`, primary key `{object_type_id, jahr}`). User decision from Phase 5e planning (see `PROGRESS.md`), not per day or globally continuous.
- **Atomic, concurrency-safe assignment**: `POST /object-types/{id}/next-kennzeichen` executes `INSERT ... ON CONFLICT DO NOTHING` (creates the counter row if needed, without two simultaneous first calls failing on a unique constraint), followed by `SELECT ... FOR UPDATE` (locks the row for the duration of the transaction) — parallel calls are thereby serialized instead of overwriting each other. Verified live against the real stack as well as via an `asyncio.gather` test with five concurrent calls (guaranteed to yield `001`–`005`, no duplicates/gaps).
- **Who actually triggers the assignment and where `Kennzeichen` ends up** (reserved attribute key, `403` on subsequent modification without the `dms-admin` role) is the responsibility of the Document Service (**P5e-S2**, see `docs/services/document-service.md`) — this service only delivers the fully rendered string on request, without itself knowing whether/where it is used.
- **Global display default** (`kennzeichen_config`, since **P5e-S3**): `GET`/`PUT /kennzeichen-config` manage a single toggle `show_before_filename` (default `true`) — the effective value per document type as resolved by the frontends is `kennzeichen_display_override` (if not `null`), otherwise this global default. The resolution itself happens **client-side** in Admin UI/User UI, not here — this service only delivers the two raw values, no dedicated "resolved" endpoint (no consumer would need it synchronously enough to justify the additional roundtrip).

## Minimum Signature Level (3.10, since P6-S7)

`required_signature_level` (`null`/`"ses"`/`"aes"`/`"qes"`) is, like `kennzeichen_format`/`kennzeichen_display_override`, only allowed for document classes (`422` for folder classes, same validation function as the other document-class-exclusive fields). This service **does not enforce the level itself** — it only delivers it on request; the actual enforcement happens on every signing operation in `signature-service` (queried there via `GET /object-types/{id}`, `400` on a requested level that is too low), see `docs/services/signature-service.md`.

## Form Layouts (2.2b, since P5b-S2)

In addition to its attributes, every object type carries a form layout per usage purpose (`display`/`search`/`upload`) — a row/column grid that controls how the attributes are arranged in the User UI (metadata display, search form, upload dialog, all actually wired up only from P5b-S4 onward).

- **Smart layout generation** (`object_type_service.layout.generate_smart_layout`): packs an object type's attributes into rows of two fields each, in creation order; initially adopts the technical attribute name 1:1 as `label` and mirrors the attribute's `required` flag at generation time. The same heuristic applies to all three usage purposes.
- **Generated, not persisted**: Without a saved override, `GET .../layouts/{purpose}` returns, on every call, a layout freshly computed from the current attribute list (`is_custom: false`) — it thereby stays automatically up to date, even if attributes change later. Only a `PUT` explicitly freezes a state (`is_custom: true`, a snapshot rather than a live reference). **Detailed rationale: ADR 0014.**
- **Reference check on save**: `PUT` rejects a layout (`422`) that references an attribute not belonging to the object type — analogous to the `allowedParentTypes` reference check (2.2a).
- **`DELETE` resets a single usage purpose specifically** to the generated smart layout — idempotent, no error if an override never existed.
- ~~No GUI editor in this session — the guided attribute selection/display-name assignment/layout fine-tuning in the Admin UI follows with **P5b-S3**~~ — **closed at P5b-S3**: `admin-ui`'s `ObjectTypeEditor` + `LayoutDesigner`, see `docs/services/admin-ui.md`. Stale bullet, left un-struck when that session shipped.
- **No retroactive check**: If an object type's attribute list changes after an individual layout has already been saved, that layout is not automatically updated (it may afterward reference a removed attribute) — the same deliberate limitation as with `allowedParentTypes` (ADR 0013).

## Retention & Deletion Reason Requirement (5.2/5.2a, since P7-S1)

`default_retention_days` and `deletion_reason_required_override` are — unlike `kennzeichen_format`/`required_signature_level` — **not** restricted to `applies_to="document"`, but allowed for document **and** folder classes alike, since `document-service` (P7-S1, documents) and the planned successor P7-S1b (folders) use the same object type schema for their respective retention default. Only validation: `default_retention_days` must be `>= 0` (`422` otherwise) — no reference check like with `allowedParentTypes`.

Both fields are read by `document-service` (`GET /object-types/{id}`), not evaluated by this service — analogous to `required_signature_level`, which is likewise only stored here but enforced by `signature-service`. `default_retention_days` determines, at document creation without a manually set `retention_until`, a one-time `created_at + default_retention_days` date (no repeated lookup on a later type change); `deletion_reason_required_override` — if not `null` — overrides the global `RetentionConfig.deletion_reason_required` default from `document-service`.

## Records Disposal & Long-Term Archiving (5.6, since P7-S3)

`default_archive_after_days`/`archive_encryption_enabled` follow exactly the same pattern as `default_retention_days`/`deletion_reason_required_override` above — allowed for both `applies_to` values, no reference check, `default_archive_after_days` must be `>= 0`. Read exclusively by `document-service` (resolving `Document.archive_after` at creation, analogous to `retention_until`) and `archival-service` (`GET /object-types/{id}`, to decide whether a document's archive copy must be encrypted) — this service itself does not evaluate either field. Unlike `deletion_reason_required_override`, `archive_encryption_enabled` is deliberately **not** a tri-state (no global default it could override) — a pure yes/no configuration per object type.

## Classified Document Classification (2.5, since P15-S1, multi-level since P17-S2/14.2)

`classification_level` (string, nullable) — only allowed for `applies_to="document"` (`422` otherwise, same validation pattern as `kennzeichen_format`/`required_signature_level`), one of the four common German VS classification levels (`VS-NfD`/`VS-VERTRAULICH`/`GEHEIM`/`STRENG GEHEIM`, taken literally from concept text 14.2) or `null` = not classified. **Up to P17-S1 a pure boolean** (`is_classified`) — every set value (regardless of the specific level) still triggers the same gate, the level itself is purely additional information, see [ADR 0059](../adr/0059-egov-paket-aktenplan-hierarchie-und-mehrstufige-vs-einstufung.md). [ADR 0051](../adr/0051-papierkorb-familie-classification-via-object-type-scoped-global-endpoints.md) explains the original decision for a schema-bound rather than attribute-value-based marking — P17-S2 does not change this pattern, only the value space was extended. The `GET /object-types` filter parameter `is_classified: bool` is nonetheless still named that way (not `classification_level`) — callers ask "any classification or none", never a specific level; internally translated to `classification_level IS (NOT) NULL`. **Since Post-Roadmap Phase 31 Session 3** ([ADR 0114](../adr/0114-per-document-classification-level.md)): this field is now only a per-class *default*, copied once onto a new document at creation time — `document-service` no longer calls this filter to resolve its classified-documents trash (that now reads the document's own `classification_level` directly, see `docs/services/document-service.md` "Classification Level"); a document's classification is independently settable/raisable afterwards, decoupled from the object type.

## Constraint Engine (4.5)

The actual validation logic lives in `libs/dms-constraint-engine` (a pure, stateless library) — **see ADR 0003** for the rationale behind why this is not its own service. This service is the only place that imports the lib; other services (Document Service, Folder Service) exclusively call `/object-types/{id}/validate` over HTTP.

Supported (minimum per 4.5): required fields, conditional required fields, pattern checking (regex) for values and names, value ranges (`min`/`max`), as well as, since P5b-S1, the placement hierarchy from 2.2a (`allowedParentTypes`/`parent_type_name`, see above).

`type: "reference"` checks the format (non-empty string), never the actual existence of the referenced value — this engine stays DB/HTTP-free by design (ADR 0003), a generic "reference type → any service" resolution does not exist. **Since P64-S1** ([ADR 0193](../adr/0193-cross-service-reference-validation.md)): an attribute definition MAY additionally declare `reference_target` (`"document"`/`"folder"`, validated at object-type save time, see "Open Points" below for the historical framing) — the calling service (`document-service`/`folder-service`) then performs the actual existence check itself once this engine's own shape check passes. Omitting `reference_target` preserves the original format-only behavior unchanged.

**Not checked**: whether a calling service (Document/Folder Service) actually uses an `applies_to` value matching its own type (e.g. a document with a `"folder"` object type) — this is the calling service's responsibility.

## Status-Transition Rules (4.5/7.1, Phase 45 Session 4)

4.5 itself says the rule engine evaluates conditions "on creation, modification, **and status transitions**"; 7.1 (the workflow engine) says a BPMN process carries a business object through its steps "including the associated status transitions ... and constraint checks (2.2) at each step." Neither section ever defined a concrete status vocabulary — this was genuinely greenfield, split out of the former P45-S3 (see [ADR 0165](../adr/0165-ocr-review-manual-task-workflow-integration.md)) precisely because it needed its own design decision before it could be built. See [ADR 0166](../adr/0166-status-transition-constraint-engine.md) for the full rationale.

**`status_transitions`** (`[{"from": str, "to": str, "requiredAttributes": [str]}]`) lives on the object type, same "absence = no restriction" default as `allowedParentTypes`. `requiredAttributes` may only reference attribute names that actually belong to the object type (422 otherwise, same "no orphaned field references" check as `object_type_layout`'s own attribute references) — the only rule type built so far, mirroring the plain `required` attribute check but scoped to a specific transition instead of unconditionally.

`POST /object-types/{id}/validate` accepts optional `from_status`/`to_status` — when given, `dms_constraint_engine.validate()` additionally checks every `status_transitions` entry matching that exact pair, appending an error per missing `requiredAttributes` entry. Both unset (the default, matching every call site before this session) skips this check entirely — unchanged behavior for creation/modification/placement validation.

**The one real caller so far**: `case-service`'s `Case.status` "open"→"closed" transition — the first (and, for now, only) real status-transition trigger anywhere in this codebase (see `docs/services/case-service.md`). `object-type-service` itself has no notion of object-type *instances* with a status; `status_transitions` only becomes meaningful once some other service's own status field opts into calling this endpoint with it.

## Events

None — a pure reference-data service, queried synchronously over HTTP, not consumed/published via events.

## Self-Registration (Concept 3.2a, since P4-S1)

Registers itself with the registry on startup (`libs/dms-registry-client`: register, periodic heartbeat, deregister on shutdown) — the basis for the API gateway's routing (`docs/services/gateway-service.md`). Opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`; without both values the service runs unchanged, without discovery.

## Sensors (Concept 10.1)

None yet — follows in Phase 11.

## Tests

- `uv run pytest services/object-type-service/tests`: Repository (CRUD, `allowed_parent_types`/`icon` validation including rejection of unknown/non-folder references, layout upsert/reset/attribute reference check, reference-number-generator format validation, yearly counter including independence per object type, plus an `asyncio.gather` concurrency test with five parallel calls, global reference-number configuration including default), smart layout generation (`test_layout.py`, pure function logic), API (`/validate` including `parent_is_root`/`parent_object_type_id` resolution, 422 on invalid 2.2a fields, `/layouts/{purpose}` including generated vs. saved layout, 422/404 cases, `/next-kennzeichen` including 404 without a format, `/kennzeichen-config` GET/PUT). Since **P6-S7** additionally: `required_signature_level` rejected on folder classes (422), persisted on document classes (repository + API). Since **P7-S1** additionally: `default_retention_days`/`deletion_reason_required_override` persisted for document **and** folder classes, `default_retention_days < 0` rejected (422). Since **P7-S3** additionally: `default_archive_after_days`/`archive_encryption_enabled` persisted for document **and** folder classes, `default_archive_after_days < 0` rejected (422). Since **P15-S1** additionally: classified document classification only allowed for document classes (422 for `applies_to="folder"`, both at creation and modification), `GET /object-types` extended with a filter (used by `document-service` to resolve the classified-documents trash). Since **P17-S2** additionally: `classification_level` only accepts the four valid VS levels (422 on unknown value), default `null`; attribute-based reference-number placeholders (unknown placeholder → 422 on saving the format, `{Federführung}` successfully saved/rendered, `POST .../next-kennzeichen` with a passed attribute value returns the expected prefix, missing attribute value despite referenced placeholder → 422). Since **Post-Roadmap Phase 38 Session 3** additionally: this service's first-ever `test_api.py` RBAC coverage — 401/403 for `POST /object-types`, 403 for `PUT`/`DELETE /object-types`, `PUT /object-types/{id}/layouts/{purpose}`, `PUT /kennzeichen-config` each without `admin.object_config`, plus a regression test confirming `POST .../validate`/`POST .../next-kennzeichen` remain ungated. Since **Phase 45 Session 4** additionally: `status_transitions` CRUD (creation/update with a valid entry, 422 for an unknown referenced attribute, 422 for a missing `from`/`to`, 422 for an identical `from`/`to`) and `/validate` with `from_status`/`to_status` (blocked, allowed, and the no-status-transition-requested case unaffected). Since **P64-S1** additionally: `reference_target` accepted on a `reference`-type attribute (`"document"`), omitted (format-only, unchanged), rejected when set to an unknown value (422), and ignored (no validation) when set on a non-`reference` attribute. **109 tests since P64-S1** (previously 104).
- **Live smoke test** (P5e-S1): `docker compose build object-type-service` + `up -d`, an object type with `kennzeichen_format` was created, two `POST .../next-kennzeichen` calls returned `2026-001`/`2026-002`, a third object type without a format returned `404` — test data subsequently deleted again.
- **Live smoke test for status-transition rules** (Phase 45 Session 4): created a real object type with a `status_transitions` entry requiring an attribute, `POST .../validate` with `from_status`/`to_status` correctly rejected (missing attribute) and accepted (attribute present) — full end-to-end verification (including `case-service`'s two call sites) documented in `docs/services/case-service.md`.

## Open Points

- ~~Reference existence check (see above) not implemented.~~ — **closed in P64-S1**
  ([ADR 0193](../adr/0193-cross-service-reference-validation.md)): a new optional `reference_target`
  (`"document"`/`"folder"`) key on a `type: "reference"` attribute, format-validated here at object-type
  save time (`_validate_reference_targets`); the actual existence check happens in the calling service
  (`document-service`/`folder-service`, the only two instance-holding callers), which is where it belongs
  — this engine stays DB/HTTP-free (ADR 0003).
- ~~Status transitions (4.5 mentions "at creation, modification, and status transitions") are not yet evaluated~~ — **closed in Phase 45 Session 4**, see "Status-Transition Rules (4.5/7.1)" above and [ADR 0166](../adr/0166-status-transition-constraint-engine.md).
- **No admin-UI editor for `status_transitions`** (Phase 45 Session 4) — configured API-only for now, same "backend-first, UI follows later if a real need appears" precedent as `allowed_parent_types`/`icon` before P5b-S3.
- ~~No GUI editor for `allowed_parent_types`/`icon`/form layouts/reference number generator~~ — **closed at P5b-S3** (`allowed_parent_types`/`icon`/form layouts) and **P5e-S3** (reference number generator format/display override), both in `admin-ui`'s `ObjectTypeEditor`, see `docs/services/admin-ui.md`. Stale bullet, left un-struck when those sessions shipped.
- No retroactive check and no cycle detection for `allowed_parent_types` (see ADR 0013); no retroactive check for saved layout overrides on later attribute changes (see ADR 0014). **Re-examined in Post-Roadmap Phase 38 Session 1** (alongside fixing a genuine, unrelated instance-level move-cycle bug in `folder-service`) and reconfirmed as a deliberate, still-correct design decision, not a bug: `allowed_parent_types` cycle checking is about a *type-graph* relationship (type A and type B configured to allow each other as a parent class), which ADR 0013 already reasoned fails safely at the first actual placement attempt with no silent failure state — unlike an unchecked `parent_id` *instance* chain, a type-graph cycle cannot loop indefinitely or leave real folders undeletable. No change made here.
- User UI consumption of the layouts (switching the metadata panel/search form/upload dialog to layout-driven rendering) follows only with P5b-S4.
- No retroactive check if `kennzeichen_format` is changed afterward — already assigned reference numbers keep their old format.
- **No server-side "resolved display" endpoint** for `kennzeichen_config`/`kennzeichen_display_override` — every frontend resolves override-vs-default itself (see "Reference Number Generator" above). **Investigated and declined at P42-S3**: the premise that ≥3 frontends duplicate this logic did not hold — only `user-ui` (`lib/kennzeichen.ts`) actually implements it, and `object-type-service` never sees an assigned Kennzeichen value again after generation, so a server-side endpoint would trade `user-ui`'s current zero-latency local resolution for either N+1 calls or a new batch design, solving a problem that doesn't exist. Stays as-is unless a genuine second consumer appears.
