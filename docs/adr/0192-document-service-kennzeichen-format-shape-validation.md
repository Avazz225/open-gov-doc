# 0192 — document-service: `Kennzeichen` attribute writes validated against the configured format shape

**Status:** accepted
**Context:** P63-S3 (Phase 63, third session of the seventh gap-analysis round). Found by the round's
`docs/services/*.md` Open Points sweep: `PATCH /documents/{id}` gated a manual `Kennzeichen` attribute
change by role (`kennzeichen_admin_role`, `_has_kennzeichen_admin_role`) but never validated the new
value against the object type's own configured `kennzeichen_format` — a `dms-admin` could write an
arbitrary string that breaks the reference-number contract other services rely on (`mail-connector`'s
candidate matching, `migration-service`).

## Decision

New `_kennzeichen_format_to_pattern(kennzeichen_format)` helper in `document-service`'s own `main.py`,
converting a `kennzeichen_format` template (e.g. `"{YYYY}-{Laufende_Nummer}"`) into a regex that a
manually-written value must match. The five fixed date/counter placeholders (`YYYY`/`YY`/`MM`/`DD`/
`Laufende_Nummer`) map to their real rendered shape (`\d{4}`/`\d{2}`/`\d{2}`/`\d{2}`/`\d{3,}`, mirroring
`object_type_service.repository._render_kennzeichen`'s own formatting exactly — `Laufende_Nummer`'s
`f"{n:03d}"` is a MINIMUM width, not a truncation, so the pattern allows more than 3 digits). Any OTHER
placeholder is assumed to reference an attribute of the object type (P17-S2, e.g. `{Federführung}`) and
is matched permissively (`.*?`) — its actual value shape is unconstrained, a free-form attribute could
legitimately hold arbitrary text.

`update_document` applies this check only when `Kennzeichen` actually changes to a non-empty value AND
the object type has a `kennzeichen_format` configured — clearing the field, or a document with no
object type or no configured format, is left unvalidated (nothing to check against).

**Deliberately duplicated in `document-service` rather than calling out to `object-type-service`** for
this — the same "duplicate on purpose" convention this project already uses elsewhere for small,
self-contained per-service logic (e.g. ADR 0006), avoiding a new cross-service round trip on every
attribute write for a check this small.

## Rationale

- **Why the plan's own original framing ("reuse the existing `kennzeichen_format` regex validation
  already built for the auto-generation path") turned out to be wrong**: investigated first, found no
  such regex-matching mechanism exists anywhere in this codebase — `object-type-service`'s
  `_validate_kennzeichen_format` validates the TEMPLATE's own shape (that `{Laufende_Nummer}` is
  present, that placeholders are recognized), and `_render_kennzeichen` only ever GENERATES a value from
  the template, never validates an arbitrary value AGAINST it. This session builds the missing piece
  rather than reusing something that didn't exist.
- **Why attribute-name placeholders are matched permissively instead of rejected/strictly typed**: a
  placeholder like `{Federführung}` can reference any attribute on the object type, of any type
  (string/number/date/etc.), with no fixed shape this function can know without a full schema lookup and
  per-type pattern derivation — a materially larger feature than this session's scope. Accepting
  anything for that segment is the conservative choice: it can't falsely reject a legitimate value, only
  fails to catch a malformed one in that specific segment (an accepted, narrower gap than the one this
  session closes for the date/counter placeholders, which ARE fully validated).
- **Why `Laufende_Nummer` allows more than 3 digits**: confirmed by reading `_render_kennzeichen`
  directly — `f"{n:03d}"` zero-pads to a MINIMUM of 3 digits, it does not cap the counter at 999. A
  pattern of exactly `\d{3}` would incorrectly reject a legitimate, real value once any object type's
  counter grows past 999.
- **Why this validates only on the manual `PATCH` path, not creation**: document creation's own
  `Kennzeichen` value is always server-generated via `next_kennzeichen` (client-supplied values are
  discarded, see `test_create_document_discards_client_supplied_kennzeichen`) — there is no manually-
  entered value to validate at that point, the generator itself is already guaranteed to produce a
  matching shape.

## Consequences

- `PATCH /documents/{id}` now `422`s when a role-authorized `Kennzeichen` change doesn't match the
  configured format's shape (fixed placeholders only; attribute-referencing placeholder segments remain
  unconstrained).
- New tests: `test_update_kennzeichen_rejects_a_value_not_matching_the_configured_format`, `test_update_
  kennzeichen_accepts_a_value_matching_the_configured_format`, `test_update_kennzeichen_accepts_a_
  larger_laufende_nummer_than_the_minimum_width`, `test_update_kennzeichen_with_attribute_placeholder_
  accepts_any_shape_for_that_segment`, `test_update_kennzeichen_clearing_the_value_needs_no_format_
  match`. `document-service` 407/407 (+5).
- Rebuilt/redeployed. **Live-verified against the real running stack**: a real object type with
  `kennzeichen_format="{YYYY}-{Laufende_Nummer}"`, a real document (auto-generated `Kennzeichen` "2026-
  001") — a manual write of a garbage, non-matching value confirmed `422` with a clear message naming
  the configured format; a correctly-shaped value ("2026-999") confirmed `200`.
- `docs/services/document-service.md` updated.
