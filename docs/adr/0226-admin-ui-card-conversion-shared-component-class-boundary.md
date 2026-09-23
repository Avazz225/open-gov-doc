# 0226: admin-ui — `.card` Conversion, a Real Layout Regression Fixed, and Where the Shared-Class Conversion Stops

## Status

Accepted (P75-S9).

## Context

[ADR 0225](0225-admin-ui-shared-class-bulk-conversion-strategy.md) converted the four always-standalone
shared classes (`.hint`, `.error-text`, `.empty-state`, `.actions`). This session continues with
`.card` and closes out the "convert shared classes" line of work for admin-ui with a deliberate
stopping point.

## Decision

**`.card` converted.** Usage confirmed via grep: 57 occurrences across 31 files as bare
`className="card"`, plus exactly one combined usage (`className="card dashboard-widget"` in
`DashboardWidgets.tsx`). The 57 bare usages were bulk-replaced with the equivalent Tailwind string
(`rounded-lg border border-border p-4 mb-6`). The one combined usage was handled directly rather than
preserved as a class combo: `.dashboard-widget`'s only job was cancelling `.card`'s `margin-bottom`
inside a CSS grid (ADR 0224's own cascade-layer note on why a `mb-0` utility couldn't safely override
unlayered `.card`) — now that `.card` itself is gone as a named class, that whole problem disappears;
`DashboardWidgets.tsx` simply omits the margin utility for that one element
(`rounded-lg border border-border p-4`, no `mb-6`), and both `.card` and `.dashboard-widget` were
deleted from `globals.css` together.

**A real layout regression from [ADR 0225](0225-admin-ui-shared-class-bulk-conversion-strategy.md)
found and fixed**: that session's `.hint` → `text-sm opacity-80` conversion silently broke
`.form-grid > .hint`'s "span the full grid width" rule (a `grid-column: 1 / -1` declaration) for every
hint paragraph that was a *direct child* of a `.form-grid` container — invisible to `tsc`/`eslint`/
Vitest/production build, only visible as an actual rendered layout. A structural, indentation-based
scan across all 18 `.form-grid`-using files found **7 affected elements across 6 files**
(`ExportSettings.tsx` ×3, `OcrSettings.tsx`, `UploadSettings.tsx`, `UserManagement.tsx`,
`UserTracking.tsx`), each fixed with an explicit `col-span-full`, re-verified via a computed-style
probe (`hint width == grid width`) against the real rebuilt Docker image before the now-dead
`.form-grid > .hint` CSS rule was deleted. `RetentionSettings.tsx`'s similar-looking hint was checked
and confirmed NOT affected — its hint sits inside `.deletion-reason-catalog` (a grandchild of
`.form-grid`, not a direct child), which has its own, unrelated `grid-column: 1 / -1` rule that was
never touched.

**One test fixed for the same reason, not a new bug**: `retention-settings.test.tsx` located its two UI
sections via `closest(".card")`, an implementation-detail CSS class selector. Updated to `closest("div")`
— both sections are one `<h3>`'s immediate parent `<div>`, a stable structural selector that doesn't
depend on styling class names. No other test file used this pattern (confirmed via grep).

**Deliberate stopping point for the remaining shared classes**: `.data-table` (+ its `th`/`td` child
rule), `.badge` (+ `.ok`/`.down` modifier variants), `.form-grid` (+ its `label` descendant rule),
`.attribute-row`, `.checkbox-group` (+ `label` descendant rule), `.layout-row`, `.layout-field` (+
`label` descendant rule), `.deletion-reason-catalog*` all involve a descendant or compound selector
targeting **child** elements, not a single element's own class list — categorically different from the
classes converted in P75-S8/S9. Converting these to literal Tailwind utility strings would mean adding
explicit classes to every `th`/`td` and every nested `label` across dozens of files, for no visual or
architectural benefit: these classes already resolve to the same `--dms-*` design tokens the Tailwind
utilities use, so they are already visually consistent with the rest of the redesigned app. They stay as
real, permanent, shared component classes — a legitimate, Tailwind-documented pattern for exactly this
kind of widely-reused structure, not unfinished scope.

## Consequences

- admin-ui's Tailwind rollout is considered **functionally complete** as of this session: the shell
  (P75-S7) plus every single-element shared class (P75-S8/S9) are converted; the remaining classes are a
  deliberate, documented architectural choice, not deferred work.
- Any future admin-ui session that converts a `.form-grid`-adjacent element must check whether it's a
  direct child relying on `.form-grid > .hint`-style positioning before removing the class it depends
  on — this exact mistake already happened once.
- `user-ui` needs its own structural survey (same lens as ADR 0225) before its rollout plan is written,
  and should watch for the same category of "converting a class breaks an unrelated descendant-selector
  rule" regression.
