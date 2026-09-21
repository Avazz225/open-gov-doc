# 0142 — XDOMEA import hardening for genuine third-party packages

**Status:** accepted (P34-S4, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 34 Session 4 (XDOMEA/XJustiz completion, last session of the phase), affects `archival-service`

## Decision

`xdomea.parse_abgabe_message` (the `Abgabe.Abgabe.0401` import-direction reader, ADR 0128) is hardened
for three concrete gaps found by re-reading the vendored XDOMEA 4.0.0 schema against the module's own
honest "not a claim of full third-party-XDOMEA generality" scoping note:

1. **`Akte`-wrapped `Vorgang` hierarchies** are now recognized — a top-level `Schriftgutobjekt/Akte`'s
   own Betreff/UUID names the case, and its nested `Vorgang`'s documents (previously invisible to a
   direct-child-only XPath) are found.
2. **More than one top-level `Vorgang`** (`Schriftgutobjekt` is `maxOccurs="unbounded"`) no longer
   silently keeps only the first one's Betreff — all found Betreffe are combined into one display name,
   and all documents across all of them are still collected.
3. **A `Dokument` with no retrievable primary-document content** (`DokumentType.Version` is
   `minOccurs="0"` — schema-legal, e.g. a metadata-only reference to a never-digitized physical record)
   is now **skipped**, not a `ParseError` that rejects the entire package. A new
   `skipped_document_count` field (threaded through `ImportResult`/`XdomeaImportResultOut`/
   `POST /xdomea/import`'s response) reports how many were skipped, so nothing vanishes silently.

As a related tolerance fix: among a document's multiple `Version` entries (a genuine version history,
`maxOccurs="unbounded"`), the LAST one (by document order) that carries a `Format`/`Primaerdokument` is
now used, not blindly the first.

`Teilvorgang`/`Teilakte` (nested sub-Vorgänge/sub-Akten — a distinct element name from `Vorgang`/`Akte`
even though they share the same type) and the `DokumentMitSchriftstueck` choice member (a document with
nested physical-page/Schriftstück scans) remain explicitly out of scope — their nested `Dokument`
elements are still picked up by the unconditional `//Dokument` descendant search (no document is
silently dropped), but they contribute no case-naming metadata of their own.

~~The above two gaps~~ — **partially closed in Phase 42 Session 1**: `parse_abgabe_message()` gained a
Teilvorgang/Teilakte Betreff fallback and a new Schriftstueck-import loop (`skipped_schriftstueck_count`).
The BROADER redesign this ADR itself flags below (full recursive nesting, genuine multi-level hierarchy
support) remains genuinely open — Phase 42 Session 1 closed the narrow, evidence-based tolerance gap
named here, not the larger scope named in "Rationale" below, which is still correctly tracked as a
separate, deliberately-deferred item (see `IMPLEMENTATION_PLAN.md`'s "Deliberately Not Included" lists).

## Rationale

- **Bounded, evidence-based scope, not a rewrite into a general-purpose XDOMEA reader** — each of the
  three fixes was chosen because the real vendored schema (`xdomea-Baukasten.xsd`/
  `xdomea-Nachrichten-AbgabeDurchfuehren.xsd`) explicitly permits it (`Schriftgutobjekt`'s own
  `Akte`|`Vorgang`|`Dokument` choice, `maxOccurs="unbounded"` on `Schriftgutobjekt`, `minOccurs="0"` on
  `Version`), not because of a hypothetical. The two deliberately-skipped cases (`Teilvorgang`/`Teilakte`
  nesting, `DokumentMitSchriftstueck`) are real schema features too, but adding full recursive hierarchy
  support and the physical-Schriftstück substructure is a materially larger scope than "harden for at
  least multi-Vorgang packages and more tolerant structure detection" calls for — deferred, not silently
  dropped, and documented as such in the code.
- **Skip-not-reject for missing content is the single highest-impact fix** — the previous behavior meant
  ONE metadata-only `Dokument` anywhere in an otherwise-perfectly-importable third-party package failed
  the whole import with a `422`. Since `document-service` cannot create a `Document` without file
  content anyway, skipping is the only sensible alternative to outright rejection; `skipped_document_count`
  ensures the caller can see it happened instead of documents silently disappearing.
- **Combining multiple Vorgänge's Betreffe (rather than requiring a picker, or rejecting multi-Vorgang
  packages outright)** — this project's `Case` model maps 1:1 to a single Vorgang, so several
  structurally-independent Vorgänge in one package cannot become several distinct Cases within a single
  import call without a larger redesign (out of scope here, same reasoning ADR 0128 already gives for
  not building a process-definition picker). Joining the Betreffe keeps the existing "one call → one
  target case" model while surfacing all the source information instead of arbitrarily picking one
  Vorgang and hiding the rest.
- **XJustiz's `parse_uebermittlung_schriftgutobjekte` was deliberately NOT touched this session** — the
  plan scoped this session to `parse_abgabe_message` (XDOMEA) specifically; XJustiz's own equivalent
  hardening, if ever needed, is a separate, independent piece of work.

## Consequences

- **Tests**: `archival-service` 138 total (up from 131) — `test_xdomea.py` gained 6 new unit tests
  (a document without a Dateiname is skipped not raised, a document with zero `Version` elements is
  skipped, the latest of several versions wins, an Akte-wrapped Vorgang's Betreff/UUID is read correctly
  and its nested document is found, multiple top-level Vorgänge's Betreffe are combined and all their
  documents collected) and one existing test (`test_parse_dokument_element_raises_parse_error_...`) was
  rewritten to assert the new skip behavior instead of a raised `ParseError`. `test_api.py` gained one new
  end-to-end test (`test_import_xdomea_skips_a_document_with_no_retrievable_content`) posting a
  hand-crafted, schema-valid, mixed-content package to `POST /xdomea/import` and asserting a partial,
  successful import with the correct `skipped_document_count`; one existing exact-body-equality assertion
  was updated for the new response field.
- **Live-verified against the real, rebuilt running stack**: a genuinely hand-crafted third-party-shaped
  package (a `Vorgang` moved inside a real `Akte` element this module's own export never produces, plus a
  second, metadata-only `Dokument` with no `Version` at all) was POSTed directly to the rebuilt
  `archival-service` container's `POST /xdomea/import`. Result: a real new case was created via a real
  `process_definition_id`, correctly named after the Akte's own Betreff ("Drittanbieter-Akte Live-Test",
  not the nested Vorgang's own, different Betreff), exactly one real document was created (the
  metadata-only one correctly absent), and the response reported `"skipped_document_count": 1`. The test
  document was trashed afterward via `document-service`; the test case was deliberately left in place (no
  case delete/purge endpoint exists, same established precedent as P34-S1/S3's own live verifications).
- **No frontend change** — this session is backend-only, per the plan's own scoping (no UI mentioned for
  P34-S4); the existing `user-ui` XDOMEA import form (ADR 0141) is unaffected and automatically benefits
  from the more tolerant parsing without any change on its side.
- **Phase 34 (XDOMEA/XJustiz completion) is now fully complete** — see ADR 0126/0127/0128/0129/0139/
  0140/0141/0142.
