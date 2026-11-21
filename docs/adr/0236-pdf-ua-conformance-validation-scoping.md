# 0236 — PDF/UA conformance validation (veraPDF): scoped, and closed as "do not build the full integration"

**Status:** accepted
**Context:** P82-S3 (Phase 82, third and final scoping-only session — the last session of this entire
multi-phase plan). "`is_tagged_pdf()` is an approximation, no veraPDF integration" has been independently
named as a limitation by three separate ADRs (0119, 0136, 0158) across three different sessions, each
re-stating the same gap rather than resolving it. This session gives it a definitive verdict instead of
letting it keep resurfacing as a recurring "known limitation" footnote.

## What already exists, verified against current code

- **`is_tagged_pdf()`** (`services/rendering-service/src/rendering_service/export_pdf.py:42-57`) is a
  four-line boolean check: `"/StructTreeRoot" in reader.root_object` via `pypdf`. It confirms a structure
  tree root node exists — nothing about `/MarkInfo /Marked`, tag-to-content-type correctness (`<Figure>`
  vs. `<P>`), alt-text on images/links, `/Lang` presence, reading order, tab order, or font
  glyph-to-Unicode mapping. Real ISO 14289-1 (PDF/UA) conformance is a rule-engine walk against roughly 80
  machine-checkable constraints; this is a single presence check for one graph node.
- **`docs/services/rendering-service.md`'s own Open Points already names this twice** (lines 320, 327-328)
  — both the PDF/A-without-veraPDF gap and the PDF/UA-approximation gap, both honestly documented as known
  limitations, never silently claimed as resolved.
- **No JVM exists anywhere in this stack** except Keycloak, a pre-built off-the-shelf identity provider
  image, not a wrapped library (`infra/k8s/dms/templates/keycloak.yaml:67`). `rendering-service`'s own
  image (`python:3.12-slim` + headless LibreOffice invoked as a subprocess, no embedded JVM) has never
  needed one. `pyproject.toml`'s PDF stack (`pypdf`, `pymupdf`, `reportlab`) is pure Python throughout.
- **Direct precedent already exists for rejecting a JVM dependency on exactly this reasoning**: ADR 0208
  (P72-S3, OSCI-Transport scoping) explicitly considered "shelling out to a JVM sidecar running the real
  Java reference library" and rejected it as "a genuine architectural departure for an otherwise entirely
  Python/TypeScript stack, introducing a new runtime, deployment unit, and operational surface for exactly
  one connector." veraPDF would be the same departure, for a use case that is actually **more** pervasive
  (every PDF export system-wide, not one connector) — the same reasoning applies at least as strongly.
- **veraPDF's actual license needs independent verification before any future session relies on it** —
  general knowledge suggests a GPLv3/MPLv2 dual license, but nothing in this repo's ADRs states this, and
  this scoping session did not independently confirm it against veraPDF's own current licensing terms.
  Flagged here rather than asserted as fact.

## Decision

**Do not build a full veraPDF/JVM integration into `rendering-service`.** The architectural-departure cost
(new runtime, new deployment unit, new operational surface, running on every export rather than one
connector) is not justified by what's actually gained — real ISO 14289 conformance for a codebase that,
per the research above, does not clearly have a concrete requirement to actually certify PDF/UA compliance
(no eGov concept requirement or user request identified for this specifically, only the general
"accessibility" intent that P31-S8/P33's own live-screenshot-verified work already substantively serves).

**Instead, two lower-cost improvements are recommended, resolving this as "closed, not perpetually
deferred":**

1. **Extend `is_tagged_pdf()`'s Python heuristic to cover more of the cheaply-machine-checkable PDF/UA
   rules**, staying entirely within the existing pure-Python stack — no new runtime. Concretely, in a
   future low-effort session: also check `/MarkInfo /Marked` is `true`, `/Lang` is present at the catalog
   or page level, and that `<Figure>`-tagged structure elements carry `/Alt` text — the three checks with
   the best signal-to-effort ratio via `pypdf`'s existing object-graph access, no new dependency. This
   remains explicitly an approximation, not full conformance, and should keep being documented as such —
   just a measurably better approximation than a single presence check.
2. **Document an external, offline veraPDF verification path for operators who need actual certified
   PDF/UA compliance** (e.g. for a compliance audit): a short doc section pointing at running the real
   `verapdf` CLI against exported files outside this codebase's own request path — no code, no new
   runtime, but a real, complete answer for the rare installation that genuinely needs certified
   conformance rather than "close enough."

Both are optional future polish, not required by this ADR — the point of this scoping session is the
**verdict** (no JVM integration, ever, under the current requirements), not scheduling the two follow-ups
as committed work.

## Consequences

- `docs/services/rendering-service.md`'s Open Points can be updated (by whichever future session actually
  does items 1/2 above, or by a documentation-only pass) to stop describing this as an open gap awaiting a
  decision, and instead as a deliberate, reasoned scope boundary with a name to point to (this ADR) — the
  same treatment ADR 0208 already gives OSCI-Transport's JVM question.
- Any future session tempted to re-open "should we add veraPDF" should read this ADR first rather than
  re-deriving the same JVM-departure argument from scratch a fourth time.
- This closes the last open item of the Phase 78–82 plan. Per the user's own standing instruction, `graphify
  update .` runs once now, at the very end of this whole plan — not after this phase's own close alone.

## Build — P89-S3

Both recommended items are built; the verdict (no JVM integration) stands.

- **Item 1:** `pdf_accessibility_issues()` in `export_pdf.py` checks `/MarkInfo /Marked`, a catalog
  `/Lang`, and `/Alt` (or `/ActualText`) on every `/Figure`.
  - **Deviation from the wording above:** it is a separate function, not an extension of
    `is_tagged_pdf()`.
    - `is_tagged_pdf()` answers "is there a structure tree", and the export pipeline's tag preservation
      (ADR 0136) is tested against exactly that.
    - Folding three more rules into the boolean would make a tagged PDF with a missing `/Lang` look
      untagged, and would hide which rule failed.
  - `/render/pdf-tag-check` returns `issues` alongside `is_tagged`; document-service passes them through;
    the user-ui names them in a second, non-blocking hint.
  - **Only the catalog `/Lang` counts.** Pages carry no language; per-element `/Lang` without a document
    language is rarer than missing both.
  - A LibreOffice tagged export passes all three, so the checks don't flag the platform's own output.
- **Item 2:** `docs/services/rendering-service.md` "PDF/UA heuristics and the offline veraPDF path" covers
  how to export, the CLI calls for PDF/UA-1 and PDF/A, and the licence caveat.
