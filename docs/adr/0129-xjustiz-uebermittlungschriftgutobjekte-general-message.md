# 0129 — XJustiz: `uebermittlungSchriftgutobjekte`, the one representative message type, export only

**Status:** accepted (P31-S13c, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 13c (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #12), third of the P31-S13a → 13b → 13c
split ([ADR 0126](0126-xdomea-general-exchange-split-download-upload-not-federation-hub.md)), affects
`archival-service`

## Decision

`archival-service` gains `POST /xjustiz/export/documents/{id}` and `POST /xjustiz/export/cases/{id}` —
building and validating `nachricht.gds.uebermittlungSchriftgutobjekte.0005005` ("Übermittlung
Schriftgutobjekte"), the ONE XJustiz message type this session implements, per ADR 0126's "first vertical
slice" scoping. XJustiz's real, official catalog (confirmed directly against the official
`xjustiz.justiz.de` schema, current version 3.6.2) spans 30 specialized modules and 158 message types —
`uebermittlungSchriftgutobjekte` is the exception: a general-purpose, cross-cutting document/file
transmission message defined in XJustiz's own base module (Grundmodul), explicitly documented as usable
across every communication scenario ("Justiz zu Justiz", "Justiz zu Extern", "Extern zu Justiz"), not tied
to any single judicial process type — the direct XJustiz counterpart to what
[ADR 0127](0127-general-xdomea-export-abgabe-0401-synchronous-not-disposal-pipeline.md) already built for
XDOMEA. Export only, mirroring P31-S13a's own scope — an XJustiz import direction is a plausible future
session, not attempted here.

## Rationale

- **Message choice found via direct research against the real schema, not assumed**: the official XJustiz
  module listing was checked, the actual 3.6.2 schema package (`XJustiz_3_6_2_..._XSD.zip`) was downloaded
  directly from `xjustiz.justiz.de`, and `uebermittlungSchriftgutobjekte`'s own message documentation
  ("Diese Nachricht ist eine Erweiterung des Type.GDS.Basisnachricht") plus a dedicated implementation
  guide PDF ("Einheitlicher XJustiz - Strukturdatensatz für die Übermittlung von Schriftgutobjekten") were
  read before writing any code — same rigor as ADR 0029's own XDOMEA research.
- **Structurally the closest XJustiz counterpart to XDOMEA's `Abgabe.Abgabe.0401`**: `Type.GDS.
  Schriftgutobjekte` permits a standalone `dokument` (no enclosing case) or one or more `akte` — XJustiz's
  own structural unit for a case-like container, analogous to but NOT the same shape as XDOMEA's `Vorgang`
  (see "Consequences" for the concrete structural difference this caused).
- **Export only, not export+import in one session**: P31-S13a/13b already split XDOMEA's own export and
  import into separate sessions for the same reason (keeping each session's Definition of Done meaningfully
  checkable) — doing the same split for XJustiz, and stopping at export for this session (the last of
  P31-S13), keeps this session's scope proportionate to "one representative message type," not "one
  message type in both directions."
- **A real, schema-verified surprise, found only by compiling against the actual vendored schema**: an
  initial implementation attempt assumed XJustiz's `Akte` nested its documents the same flat way XDOMEA's
  `Vorgang` does. The real schema instead nests documents inside `akte/xjustiz.fachspezifischeDaten/
  inhalt/dokument` — a genuinely different structural shape, not a naming variation — caught by reading
  `Type.GDS.Akte`'s own `inhalt` sub-structure in the vendored XSD before shipping, matching this project's
  standing practice of never assuming one XÖV-family standard's shape from another's.
- **A required attribute easy to miss from the type's own `xs:sequence`**: `nachrichtenkopf`'s
  `xjustizVersion` attribute (fixed `"3.6.2"`) is declared as an `xs:attribute` on `Type.GDS.
  Nachrichtenkopf`, appended AFTER its `xs:sequence` block in the schema source — reading only the sequence
  (as XDOMEA's own root-element attributes might suggest by analogy) misses it entirely. Found live via the
  real schema validator's own rejection, not by inspection alone.
- **Generic-fallback codes do not line up across different XJustiz codelists**: `gds.dokumentklasse`'s
  "Andere / Sonstige" is code `001`; `gds.aktentyp`'s "Andere / Sonstige" is code `017` (`001` there means
  "Zivilakte"). Confirmed by fetching each codelist's real, current values independently (`gds.
  dokumentklasse` via xrepository.de's genericode API, since it's an externally-versioned Typ3 codelist;
  `gds.aktentyp` by reading the vendored XSD's own embedded Typ2 enumeration directly) rather than assuming
  either one from the other.
- **XÖV-framework files reused verbatim, not re-vendored**: `xoev-code.xsd`/`din-norm-91379-datatypes.xsd`
  are the identical shared base modules XDOMEA already vendors (`xdomea_schema/`) — copied into
  `xjustiz_schema/` rather than re-downloaded, since both standards genuinely depend on the exact same
  files (confirmed by comparing namespaces/URLs, not assumed from the shared "XÖV" branding alone).

## Consequences

- **`akte`'s documents nest differently than XDOMEA's `Vorgang`** — `general_export.py`'s XJustiz-specific
  functions (`build_document_export_package_xjustiz`/`build_case_export_package_xjustiz`) are separate,
  parallel siblings of the XDOMEA ones, not a shared abstraction — the two formats' actual field shapes
  diverge enough (nesting depth, filename convention, codelist structure) that a forced shared
  implementation would be more complex than two straightforward parallel ones, consistent with this
  project's general preference for duplication over premature abstraction when the shared behavior isn't
  actually identical.
- **No XJustiz import direction, no XJustiz frontend entry point** — same deliberate scoping as
  [ADR 0128](0128-xdomea-import-existing-or-new-case-required-process-definition.md)'s XDOMEA-import-only
  session and [ADR 0127](0127-general-xdomea-export-abgabe-0401-synchronous-not-disposal-pipeline.md)'s
  case-export-has-no-UI decision — both plausible future additions, neither attempted here.
- **This session completes P31-S13 (a/b/c) and, with it, the currently-scoped Phase 31 roadmap** — per the
  user's own standing instruction, a full gap re-analysis and a new follow-up plan is the next step, not
  further XDOMEA/XJustiz work.
