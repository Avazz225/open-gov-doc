# 0220: office-addin Tailwind Rollout — Full Component Conversion, and a Retroactive Primary-Button Border Fix Across All Six Apps

## Status

Accepted (P75-S3, first session of the small-apps rollout group).

## Context

[ADR 0219](0219-login-page-tailwind-redesign-accent-fg-token-cascade-fix.md) (P75-S2) converted only
the login page in all six apps. P75-S3 begins the "rest of each app" rollout named in
`IMPLEMENTATION_PLAN.md`'s Phase 75, starting with `office-addin` — the smallest of the four small
apps grouped together for this rollout (`office-addin`, `migration-console`, `process-designer`,
`reviewer-ui`), before `admin-ui`/`user-ui` each get a dedicated session.

## Decision

**office-addin: full conversion.** Every remaining hand-written CSS class in `globals.css`
(`.page`/`.top-bar`/`.top-bar-actions`/`.app-title`/`.section`/`h2,h3`/`.hint`/`.actions`/
`.login-form`/`.error-text`/`.empty-state`/`.entry-list`/`.entry-row`, and the `input`/`select`/
`button`/`form` tag-selector rules) is now expressed as Tailwind utility classes directly on
`Shell.tsx`, `DocumentPicker.tsx`, `TemplatePicker.tsx`, `MetadataForm.tsx`, `WorkflowPanel.tsx`,
`TaskPane.tsx`, `OfficeGate.tsx`, and `RequireAuth.tsx`. `globals.css` now holds only a genuine
minimal reset (font/box-sizing/body background/form-element font inheritance — the same handful of
rules `preflight` would otherwise provide), kept in `@layer base` per ADR 0219's cascade-ordering
fix. `.login-form` was dropped outright (already unused since P75-S2 replaced the login page markup).

**A real defect found via computed-style probing, not just visual screenshot review**: this session's
verification used the same `getComputedStyle()` static-probe technique ADR 0219 established (necessary
here too, since office-addin's Office.js host-gating still blocks a plain-browser `/login`/task-pane
load) — and it caught that every `bg-accent` primary button (the login page's submit button in **all
six apps**, plus this session's new office-addin task-pane buttons) was silently falling back to the
browser's unstyled default `<button>` border (`2px outset`, computed as `rgb(0, 0, 0)` in this
environment) instead of a deliberate style, because preflight's `button { border: 0 }` reset is
excluded (ADR 0218) and no component ever set an explicit border on these particular buttons. This was
invisible enough in a full-page screenshot review to pass P75-S2 unnoticed — exactly the kind of defect
this project's own "verify computed styles, not just look at a screenshot" discipline exists to catch.
Fixed by adding `border-0` to every `bg-accent`-filled button across all six apps (the five other login
pages' submit buttons, plus office-addin's `MetadataForm`/`TaskPane` primary buttons) — re-verified via
both the static probe and live `getComputedStyle()` checks against the real running Docker containers
for all five non-office-addin apps (`borderTopWidth: "0px"` confirmed on all five).

## Consequences

- Any future `bg-accent` (or other unstyled-by-default) button must get an explicit `border`/`border-0`
  utility, never rely on the browser's UA default — this is now the established pattern to copy.
- The remaining P75-S3+ sessions (`migration-console`, `process-designer`, `reviewer-ui`, then
  `admin-ui`/`user-ui`) should re-check every new primary button they introduce against this same
  computed-style probe, not assume a clean screenshot alone proves correctness.
- office-addin's rollout is now complete for its entire UI surface (not just the login page) — no
  hand-written CSS classes remain in this app's `globals.css` beyond the minimal reset.
