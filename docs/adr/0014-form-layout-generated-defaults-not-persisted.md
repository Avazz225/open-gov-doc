# 0014 — Form layouts: generated smart layouts stay unpersisted, only overrides are stored

**Status:** accepted
**Context:** Concept 2.2b, Session P5b-S2

## Decision

The form layout data model (row/column grid per object type and purpose `display`/`search`/`upload`) is implemented as follows:

- A new table `object_type_layout` (PK `(object_type_id, purpose)`) stores **only explicitly deviating layouts** — no entry means "no deviation from the generated default yet".
- `GET /object-types/{id}/layouts/{purpose}` returns an **on-the-fly generated smart layout** (`object_type_service.layout.generate_smart_layout`) when no entry exists, identifiable by `is_custom: false`. If an entry exists, it is returned unchanged (`is_custom: true`).
- `PUT .../layouts/{purpose}` writes an explicit override; `DELETE .../layouts/{purpose}` removes it again (reset to the generated default) — idempotent, no error if none ever existed.
- Field display names (`label`) live **in the layout**, not as a new field on the attribute definition itself — when generating, the smart layout initially copies the technical attribute name as the label.

## Rationale

- **A generated default that is persisted immediately at object-type creation automatically goes stale on every later attribute change** (new attribute added, `required` changed) — the stored copy would then have to be actively kept in sync on every attribute change, including the question of what should happen to a layout already hand-adjusted by then. Without persisting the default, this synchronization problem disappears entirely: a not-yet-customized layout is by definition always current, because it is freshly computed from the current attribute list on every read.
- **Explicit overrides are deliberately a snapshot, not a live reference.** As soon as an admin adjusts and saves the generated layout in the future layout designer (P5b-S3), exactly that state is frozen (including the `required` flags valid at save time) — subsequent attribute changes on the object type do not automatically update an already-saved custom layout. This is the same deliberate non-retroactivity as with `allowedParentTypes` (ADR 0013): consistency checking only on the respective write operation, no background reconciliation across the whole dataset.
- **Reference checking on write, not only on read**: `PUT` rejects layouts that reference an attribute that does not (or no longer) exist (`422`), analogous to the `allowedParentTypes` reference check (ADR 0013) — prevents silent, never-visible dangling references in the layout.
- **The same generation heuristic for all three purposes** (2 columns per row, attribute order) rather than three different default algorithms — per the concept, purpose-specific differences arise exclusively through individual fine-tuning in the layout designer, not through different starting layouts.
- **`label` belongs to the layout, not the attribute definition**: the concept describes assigning display names as part of layout generation ("assigns a descriptive display name per attribute … the system automatically derives a default layout from this information"), not as a standalone attribute schema field. Since an attribute could potentially have different labels in display/search/upload (unlikely, but not excluded by the data model), this fits the respective layout better than the attribute list defined once per object type.
- **A dedicated table instead of a JSON column on `object_type`**: three independently overridable/resettable layouts (one `PUT`/`DELETE` per purpose) are easier to handle with one row per `(object_type_id, purpose)` than with three nested keys in a shared JSON column, especially for the granular `DELETE` (resetting only a single purpose).

## Consequences

- New table `object_type_layout`, bound to `object_type.object_type` via foreign key (`ON DELETE CASCADE`) — deleting an object type automatically removes its layout overrides, no separate cleanup logic needed.
- No retroactivity check when an object type's attribute list changes after a custom layout has already been saved — a saved layout can afterward reference an attribute that has since been removed (the same class of inconsistency as with `allowedParentTypes`, see ADR 0013). Documented as an open point, not resolved in this session.
- Admin UI handling (layout designer for fine-tuning, display-name assignment) follows only with **P5b-S3** — this session covers exclusively the backend data model, smart-layout generation, and the read/write/reset API, verified via pytest/curl.
- User-UI consumption of the layouts (switching the metadata panel, search form, and upload dialog from hardwired forms to layout-driven rendering) follows only with **P5b-S4**.
