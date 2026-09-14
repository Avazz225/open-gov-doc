# 0137 — Automated accessibility test harness via `jest-axe`

**Status:** accepted (P33-S3, see Phase 32+ in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 33 Session 3 (accessibility completion, following up on ADR 0119/P31-S8 and
ADR 0135/P33-S1), affects `user-ui`, `admin-ui`

## Decision

`jest-axe` (the axe-core wrapper, despite its Jest-oriented name a plain, framework-agnostic
`axe(container) -> AxeResults` function plus a `toHaveNoViolations` matcher) is added as a dev dependency
to `user-ui` (required by the plan) and `admin-ui` (extended "as capacity allows"), wired into each app's
existing Vitest setup. New axe-based regression tests cover the concrete fixes from ADR 0119 (`user-ui`'s
classification/conflict/redaction badges) and ADR 0135 (`admin-ui`'s `LayoutDesigner` field-reorder
buttons). `reviewer-ui`/`migration-console` (also touched by ADR 0135) are deliberately NOT extended — see
"Rationale".

## Rationale

- **`jest-axe` over `vitest-axe`**: `vitest-axe` is a thin, early-stage fork (npm shows a single published
  `0.1.0`, no meaningful adoption); `jest-axe` is the long-established, actively maintained standard (11
  major versions, huge ecosystem usage) and works with Vitest without friction — Vitest's `expect` is
  Jest-API-compatible, so `expect.extend(toHaveNoViolations)` (called manually in each app's
  `tests/setup.ts`, since `jest-axe` has no dedicated Vitest entry point unlike `@testing-library/jest-dom/
  vitest`) registers the matcher correctly. Verified empirically before committing to this choice: a real
  `npm install` + `tsc --noEmit`/`eslint`/`vitest run`/`next build` pass in both apps, confirming no
  practical friction from `jest-axe`'s Jest-flavored packaging (its own `jest-matcher-utils` dependency is
  used purely for diff-formatting internals, not a Jest runtime; `@types/jest-axe`'s transitive `@types/
  jest` dependency — needed because `@types/jest-axe` itself has no Vitest-specific declarations — adds
  ambient Jest globals to the project, but every existing test file already imports `describe`/`it`/
  `expect` explicitly from `"vitest"` rather than relying on ambient globals, so the two never collide in
  practice).
- **A small local `jest-axe-vitest.d.ts` per app, not `@types/jest-axe` alone**: `@types/jest-axe` only
  augments Jest's own `namespace jest`/`@jest/expect` module, not Vitest's `Assertion`/
  `AsymmetricMatchersContaining` interfaces — without this, `expect(results).toHaveNoViolations()` would
  fail to typecheck even though it works correctly at runtime. Mirrors the exact pattern
  `@testing-library/jest-dom/vitest`'s own bundled `types/vitest.d.ts` already uses in this project (a
  `declare module "vitest"` augmentation), just written locally instead of shipped by the library.
- **`color-contrast` disabled in every axe call** (`AXE_OPTIONS = { rules: { "color-contrast": { enabled:
  false } } }`): jsdom has no real rendering/layout engine, so axe-core cannot reliably compute rendered
  colors/contrast ratios there — a well-documented, general jsdom limitation (not specific to this
  project), confirmed by axe-core's/jest-axe's own community guidance for jsdom-based test suites. Every
  OTHER rule (labels, ARIA usage, roles, structure) still runs and is exactly what these two ADRs' fixes
  are actually about (missing `aria-label`s, badge semantics) — contrast itself was already fixed via
  direct CSS token inspection in ADR 0135's `reviewer-ui`/`migration-console` work, not something axe in
  jsdom could have verified anyway.
- **Why `reviewer-ui`/`migration-console` are NOT extended with axe here**: ADR 0135's fix in those two
  apps was exclusively a `high-contrast`-theme CSS color-token correction (`--dms-accent-bg` and a missing
  border rule) — with `color-contrast` necessarily disabled in jsdom (see above), an axe run against those
  components would provide no actual regression coverage for the fix that was made; adding the harness
  there now would be inert scaffolding, not a meaningful test. `admin-ui`, by contrast, got a real
  structural fix (missing `aria-label`s) that axe genuinely can and does catch.
- **Targeted regression tests, not a blanket "run axe on every existing test's render output" sweep**: the
  plan's own wording asks for "a regression net for the P31-S8/P33-S1 fixes" specifically — new/extended
  tests were added exactly at the fixed components (`user-ui`'s `PreviewPane.tsx` conflict/classification
  badge tests extended in place; new standalone `classification-panel.test.tsx`/
  `derived-documents-panel.test.tsx`, since neither `ClassificationPanel`/`DerivedDocumentsPanel` had a
  dedicated test file before — both are `useAuth()`-dependent components tested via the large
  `document-workspace.test.tsx` integration harness for their FUNCTIONAL behavior, but a standalone render
  is simpler and more direct for a single axe check, same precedent P33-S1's own `trash-pane.test.tsx`
  already established; `admin-ui`'s new `layout-designer.test.tsx` case). A project-wide sweep across every
  existing render in every test file would multiply CI time for no additional real coverage beyond what a
  handful of targeted, representative renders already provide - `axe()` is checking DOM structure that
  doesn't meaningfully vary render-to-render for the same component.
- **A genuine bug found while writing these tests, not just running them**: `screen.findByText("VS-NfD")`
  in the new `classification-panel.test.tsx` initially failed with "Found multiple elements" — Testing
  Library's default text matcher considers only an element's DIRECT text-node children (not full recursive
  `textContent`), so both the current-level `<p>` (whose only direct text node is "VS-NfD", the 🔒 glyph
  living in a sibling `<span>`) and the raise-`<select>`'s own "VS-NfD" `<option>` matched. Fixed by
  querying the unambiguous `aria-label` instead — coincidentally the exact accessible-name assertion the
  test already wanted to make.

## Consequences

- **New dev dependencies**: `jest-axe`, `@types/jest-axe` in `user-ui` and `admin-ui` (dev-only, confirmed
  via `next build`'s unchanged bundle sizes that neither ships into the production bundle).
- **Tests**: `user-ui` +4 (247 total, up from 244): 2 existing `PreviewPane.test.tsx` cases extended
  in-place with an axe assertion (conflict badge, classification badge), 2 new standalone files
  (`classification-panel.test.tsx` 2 tests, `derived-documents-panel.test.tsx` 1 test) — net +4 test cases
  from 3 files touched. `admin-ui` +1 (228 total, up from 227): `layout-designer.test.tsx` gained a
  dedicated axe-check case for the multi-field-row reorder-button scenario.
- **No Docker image rebuild/live-verification for this session** — every change is test-infrastructure
  only (`tests/`, `package.json`/`package-lock.json`); no `src/` production code changed in either app, so
  the built output is unaffected. `npm run build` was still run locally in both apps to confirm the
  addition doesn't break the production build pipeline itself (clean, unchanged bundle sizes).
- **`reviewer-ui`/`migration-console` remain without an axe harness** — a deliberate, documented scope
  boundary (see "Rationale"), not an oversight; a future session with a genuinely structural (non-color)
  a11y fix in either app would be the natural point to add it there too.
- **`process-designer`/`office-addin` also remain without an axe harness** — out of scope: ADR 0135 found
  nothing to fix in either app, so there is no fix to build a regression net for yet.
