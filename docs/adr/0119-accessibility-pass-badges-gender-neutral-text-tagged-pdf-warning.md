# 0119 — Accessibility pass: badge iconography/contrast, gender-neutral text, tagged-PDF export warning

**Status:** accepted (P31-S8, see Phase 31 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 31 Session 8 (eGov feature gap closure — see
[`docs/egov-feature-gap-analysis.md`](../egov-feature-gap-analysis.md), gap #11 "Accessibility"),
affects `user-ui`, `admin-ui`, `document-service`, `rendering-service`

## Decision

A dedicated research pass first surveyed the actual state across all six frontend apps (`user-ui`,
`admin-ui`, `office-addin`, `process-designer`, `reviewer-ui`, `migration-console` —
`apps/libreoffice-addin` excluded, a Python UNO extension with no web-accessibility surface at all) before
any code was written, confirming there was no prior accessibility ADR, no axe/a11y test harness, and no
prior "Barrierefrei"/BITV/WCAG mention anywhere in this project's history. Three bounded, concrete
deliverables, matching the plan's own wording exactly:

1. **Classification-related UI iconography/contrast**: every classification/conflict/redaction badge in
   `user-ui` (`ClassificationPanel.tsx`, `PreviewPane.tsx`'s version badges, `DerivedDocumentsPanel.tsx`)
   now pairs its color with a glyph and a real `aria-label` — color is never the only signal. A concrete
   high-contrast-theme bug was found and fixed (only `.badge.down` had the border override needed once
   `--dms-*-bg` tokens flatten to the page background in that theme; `.badge.classified`/`.badge.draft`
   silently lost their pill shape entirely). A second concrete bug was found and fixed: `user-ui`'s
   `globals.css` never defined `--dms-success`/`--dms-success-bg` (present in every other app), leaving
   `.badge.ok` (`BulkEditModal.tsx`) completely unstyled.
2. **Gender-neutral system messaging**: a careful, per-string review (not a mechanical find/replace) of
   every German masculine-generic person/role noun across all six apps' `de.json` files, following the
   project's own already-latent convention (`"Mitarbeitender"`/`"ausführende Person"` already existed in
   `user-ui`/`admin-ui` before this session) rather than inventing a new one.
3. **Tagged-PDF export warning**: a new `is_tagged_pdf()` check (pypdf, `/StructTreeRoot` presence) in
   `rendering-service`, exposed via `POST /render/pdf-tag-check` and proxied by `document-service`'s new
   `GET /documents/{id}/export/accessibility-check`, surfaced as a non-blocking warning next to `user-ui`'s
   "Exportieren" button whenever the source isn't (or can't become) a tagged PDF.

## Rationale

- **Badges get an icon + `aria-label`, not a new color, for the classification/conflict/redaction
  collision**: research found `.badge.classified` (classification) and `.badge.down` (version conflict)
  render in the *identical* red tone for two completely unrelated meanings, and `.badge.classified` was
  additionally reused as-is for the redaction badge (`DerivedDocumentsPanel.tsx`) — a third, semantically
  backwards meaning (a redacted copy has content *removed* for wider distribution, making it *less*
  sensitive than the original, not more). Rather than inventing new color tokens for each (a much larger,
  more speculative design-system change this session doesn't need), each badge gets a distinguishing glyph
  (🔒 classification, ⚠️ conflict, ✂️ redaction) and a real `aria-label` restating its meaning in words —
  the actual WCAG 1.4.1 fix ("use of color") is that color is never the *only* channel, not that every
  meaning needs its own hue. The redaction badge additionally gets its own `.badge.redacted` CSS class
  (neutral border, no red fill) — a genuine semantic correction, not merely an accessibility patch: it was
  always wrong to render a redacted (safer) copy in the same "handle with care" red as an actual
  classification level.
- **High-contrast border fix generalized to the whole `.badge` base class, not four separate overrides**:
  the underlying cause (`--dms-*-bg` tokens collapse to the page background in high-contrast mode, so a
  badge's pill shape has no visual cue left besides an explicit border) applies identically to every badge
  variant, not just `.badge.down` — `:root[data-theme="high-contrast"] .badge { border: 1px solid
  currentColor; }` fixes all current and future variants in one rule instead of accumulating one override
  per badge class.
- **`ClassificationPanel.tsx`'s bare, unlabeled current-level text gets an `aria-label` tying it back to
  "Einstufung"**: previously a screen reader landing on the value `<p>` out of surrounding context (e.g.
  via heading/region navigation, not top-to-bottom reading) got just the raw level string ("GEHEIM") with
  nothing connecting it to what field it belongs to.
- **Gender-neutral pass: reuse the project's own already-established convention, don't invent one** — the
  research phase found `"Mitarbeitender"` (`kontakte.hint`) and `"ausführende Person"`
  (`queryConsole.hint`) already present in the codebase before this session, both genuinely neutral
  constructions. This session's edits follow the same two patterns consistently: **substantivized
  participles** for standalone category labels referring to people as a group (`"Nutzer"` → `"Nutzende"`,
  the standard German-government convention for exactly this case — `admin-ui` nav/section/`license.users`)
  and **`"Person"`/`"Personen"`** for flowing prose describing what an individual did or is allowed to do
  (`"jeder... Nutzer"` → `"jede... Person"` in `groups.hint`/`forensicTrace.hint`; `"Nur Nutzer mit der
  Rolle..."` → `"Nur Personen mit der Rolle..."` in `user-ui`'s `kennzeichenReadOnlyHint`). `"Akteur"`
  (audit/forensic "actor" column, mirroring the backend events' own `actor` field name) was deliberately
  left as an established technical term and only *de-duplicated* against the inconsistent plain `"Nutzer"`
  used for the identical data field in one place (`reports.actor`) — not neutralized further, since
  changing an already-fixed vocabulary term used consistently as a table/CSV column header is a materially
  different, larger-blast-radius change than fixing prose.
- **Field/column labels for an identifier string are deliberately left untouched** — `"Benutzername"`/
  `"Nutzername"` (login forms, invite/delegate-by-username fields), `"Empfänger-Domain"`/`"Empfänger-
  E-Mail"` (compound nouns naming a data attribute of an email/domain, not a flowing "the recipient does
  X" sentence). German gender-neutral-language guidance (including this project's own pre-existing
  `"Nutzerkonto"`-style compounds) treats a technical compound noun differently from a prose reference to
  a person — the actual, load-bearing distinction this session applied throughout, not merely a shortcut to
  reduce the edit count. The bulk of the ~40 lines the initial regex-based research survey flagged turned
  out to be exactly this category on closer reading; the real edit count across all six apps ended up
  around 16.
- **Deliberately excludes backend service `detail=` error strings and code comments**: some backend
  German error messages surface verbatim in the UI (`ApiError.message` rendered directly, e.g.
  `ClassificationPanel.tsx`), and a handful contain gendered phrasing. Scoped out of this session — "system
  messaging" in the plan's own wording most naturally reads as UI text, and backend error strings are a
  materially different, larger-blast-radius surface (many services, not six frontend `de.json` files) that
  deserves its own deliberate pass rather than incidental inclusion here.
- **`is_tagged_pdf()` checks `/StructTreeRoot` presence via `pypdf`, not full PDF/UA conformance**: this is
  the actual technical basis of a "tagged" PDF (the structure tree a screen reader/assistive technology
  reads), not merely "is this file a PDF" — the same distinction the project's existing PDF/A-without-
  ISO-19005-validation limitation already draws for archival format compliance (`docs/services/
  rendering-service.md` "Open Points": no veraPDF integration). Malformed/unparseable input is reported as
  untagged rather than raising — content that can't even be parsed is certainly not a valid tagged PDF, and
  the warning path shouldn't itself become a new failure mode.
- **Checks the SOURCE before conversion, not the export output**: research confirmed the export pipeline
  (LibreOffice conversion for Office formats, Pillow for images, then a `pypdf`-based merge/stamp pass for
  every format including already-PDF sources) never produces a tagged PDF regardless of input — `pypdf`'s
  writer has no structure-tree-copying capability at all, so even a genuinely tagged source PDF loses its
  tags during export today. Given that, checking the *output* would always say "untagged" and teach the
  user nothing actionable. Checking the *source* — exactly what the plan's own wording asks for ("when the
  source document isn't tagged/accessible-PDF") — is the meaningful signal: a tagged source PDF getting
  detagged by this pipeline is a separate, larger fix (giving the export pipeline actual tag-preservation
  capability) that this session does not attempt.
- **Only an already-PDF source is actually checked against rendering-service; every other format is
  trivially reported untagged without a storage download or a rendering-service round trip**: an Office
  document or raster image is structurally never a tagged PDF after conversion (confirmed by the pipeline
  survey above) — there is nothing to check. `document-service`'s new endpoint shortcuts on
  `version.content_type` first, only calling `rendering-service` for the one case where the answer isn't
  already known for certain.
- **Non-blocking warning, not a confirmation dialog**: the plan asks for "an explicit warning", not a
  gate — the export button still works exactly as before; the warning is informational, shown next to the
  button (fetched once per active document/version, mirroring the existing per-document fetch pattern
  already used for redaction-preview availability etc.), not a blocking `confirm()`. `accessibilityCheck
  === null` (not yet loaded, or the caller lacks `document.read`) shows nothing — fail silent, since the
  export action itself already fails cleanly (`403`) for an unauthorized caller.
- **Same `document.read` gate as the export action itself** for the new accessibility-check endpoint — it
  reveals nothing more sensitive than the export already would (a boolean, not content), so anchoring it to
  the identical permission check the real export uses (rather than leaving it ungated, or inventing a new,
  narrower permission) is the simplest correct choice.

## Consequences

- A fresh installation's export/download behavior is completely unchanged — the warning is purely
  additive, informational, and non-blocking; nothing about the actual export output changed in this
  session (the pipeline still doesn't preserve/produce tags — see above).
- **The export pipeline itself still doesn't preserve tags on an already-tagged source PDF** — a real,
  honestly-acknowledged gap this session's warning surfaces but doesn't close. Giving `export_pdf.py`'s
  merge/stamp pass actual structure-tree preservation (or switching part of the pipeline to a library that
  can) is a materially larger, separate effort, out of this session's scope.
- **No axe-core or other automated accessibility test harness was introduced** — this session's testing
  remains manual/targeted (new `aria-label`/badge-class assertions in existing Vitest suites, a real
  `is_tagged_pdf()` unit test with a manually constructed `/StructTreeRoot` catalog entry, since neither
  `reportlab` nor `pypdf`'s writer can produce a genuinely tagged PDF to test against). A project-wide
  automated a11y test harness is a reasonable, separate future investment.
- **This session's scope is bounded to exactly the plan's three named deliverables** — it is not a general
  accessibility audit of the six apps (no skip-links were added, `aria-live` remains unused project-wide
  for async state transitions, emoji-glyph icons elsewhere in the explorer/icon-rail remain un-audited,
  `office-addin` still has no high-contrast theme at all). All confirmed, real gaps found during the
  research pass but explicitly out of this session's three-part scope — left as documented, not silently
  discovered-and-ignored.
