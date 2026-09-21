# 0140 — XJustiz frontend entry point: a distinctly-named document export button

**Status:** accepted (P34-S2, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 34 Session 2 (XDOMEA/XJustiz completion), affects `user-ui`

## Decision

`user-ui`'s `PreviewPane.tsx` gains a second inter-agency export button, "Export für Justizübergabe", right
next to the existing XDOMEA "Export für Behördenübergabe" button (ADR 0126/P31-S13a) — the mirror of that
button's exact interaction shape (toggle → inline form with a recipient-name text field → submit → ZIP
download), calling `POST /xjustiz/export/documents/{id}` (ADR 0129) instead of the XDOMEA endpoint. New
`exportDocumentXjustiz()` in `api.ts` mirrors `exportDocumentXdomea()`.

## Rationale

- **Distinct wording end to end, not just a different i18n key** — the plan explicitly called for avoiding
  ambiguity between the two now-adjacent buttons. Since the EXISTING XDOMEA button already avoids naming
  the technical protocol in its user-facing text ("Behördenübergabe"/"handoff to an authority", not
  "XDOMEA-Export"), reusing that same generic wording for XJustiz would have produced two
  visually-identical buttons with no way to tell them apart. XJustiz's own real-world audience (the
  judiciary, "Justiz zu Justiz"/"Justiz zu Extern" per ADR 0129's own research) gave a natural, equally
  non-technical, equally concise distinguishing term: "Justizübergabe"/"Justizbehörde" instead of
  "Behördenübergabe"/"Behörde". The submit button inside each inline form also got a distinct label
  ("Justiz-Paket exportieren" vs. the existing "Paket exportieren") — both forms can independently be
  toggled open at the same time (they're separate, uncoupled `useState` flags), so two identically-worded
  submit buttons could genuinely both be visible simultaneously without this change.
- **`Empfangende Justizbehörde` reuses the existing gender-neutral "Empfangende X" construction** (already
  used for `xdomeaExportLeserLabel`, "Empfangende Behörde") rather than inventing a new pattern — matches
  this codebase's own established convention (see ADR 0119/ADR 0138's gender-neutral-language work), and
  the substitution of "Justizbehörde" for "Behörde" was the only change actually needed to make it
  XJustiz-specific.
- **No new component, no new panel** — the export is a document-scoped action already living exactly where
  the equivalent XDOMEA action lives; the plan's own wording ("at least a document-export button, mirroring
  `PreviewPane.tsx`'s existing button") is satisfied without inventing new UI structure.
- ~~**A case-level export button was NOT added this session** — same reasoning ADR 0127/ADR 0129 already
  gave for XDOMEA's/XJustiz's own case-export having no `user-ui` entry point: `case-service`'s "Case"
  concept has no dedicated browsing UI anywhere in `user-ui` today, so there is no natural existing screen
  to attach a "export this case" button to. That gap (a minimal case-browsing UI) is explicitly P34-S3's
  own, separate concern, not incidentally solved here.~~ — **closed by ADR 0141** (P34-S3, the very next
  session): `CasesPane.tsx`'s case-detail view ships all four buttons (XDOMEA export, XJustiz export,
  XDOMEA import, XJustiz import).
- ~~**No import-side UI** — `POST /xjustiz/import` (P34-S1/ADR 0139) remains API-only, same scoping the
  XDOMEA import (ADR 0128) already accepted; a frontend upload flow for either format is not part of this
  session.~~ — **closed by ADR 0141**, same `CasesPane.tsx` buttons as above.

## Consequences

- **Tests**: `user-ui` +3 (250 total, up from 247) — `PreviewPane.test.tsx` gained the exact three-test
  shape already established for the XDOMEA button (submits with a named recipient and calls the API with
  the right arguments; the form cannot be submitted with an empty recipient; a failed export shows the
  generic XJustiz error message), following the identical `AsyncMock`-free, `vi.fn()`-mocked `@/lib/api`
  pattern already used throughout this test file.
- **Live-verified in a real browser** against the real, rebuilt running stack, using this project's
  existing Playwright E2E infrastructure (`e2e/fixtures.ts`'s login/API helpers) as a one-off verification
  script (not committed as a permanent E2E spec — the existing XDOMEA button itself has never had one
  either; this project's E2E suite is reserved for foundational flows, per `e2e/folder-and-document.spec.ts`
  being the only spec so far): logged in as a real user, opened a real uploaded document, clicked "Export
  für Justizübergabe", filled in a recipient name, submitted, and confirmed a real
  `<title>-xjustiz.zip` file download fired — screenshotted for visual confirmation that both buttons
  render side by side with clearly distinct labels.
- **No backend change** — this session is frontend-only, consuming the already-shipped
  `POST /xjustiz/export/documents/{id}` endpoint (ADR 0129) unchanged.
