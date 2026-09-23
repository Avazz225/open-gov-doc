# 0222: process-designer Tailwind Rollout, and a Tab-Button Default-Styling Fix

## Status

Accepted (P75-S5, third of the small-apps rollout group).

## Context

[ADR 0221](0221-migration-console-tailwind-rollout-form-font-inherit-fix.md) converted
`migration-console` in full. This session continues with `process-designer` — the largest of the four
small apps (2525 lines of TSX across its components, including the embedded `bpmn-js`/`dmn-js` designer
canvases and their properties panels).

## Decision

**process-designer: full conversion**, with one deliberate exclusion. Every remaining hand-written CSS
class (`.page`/`.top-bar`/`.top-bar-actions`/`.theme-switcher`/`.locale-switcher`/`.tab-bar`/
`.tab-button`/`.dmn-canvas`/`.hint`/`.entry-list`/`.entry-row`/`.actions`/`.login-form`/`.error-text`/
`.success-text`/`.empty-state`/`.data-table`/`.badge`/`.maintenance-banner`/`.designer-*`) converted to
Tailwind utilities directly on `ThemeSwitcher.tsx`, `LocaleSwitcher.tsx`, `MaintenanceBanner.tsx`,
`RequireAuth.tsx`, `ProcessDefinitionList.tsx`, `DmnDefinitionList.tsx`, `page.tsx` (home tab bar),
`designer/page.tsx`, `dmn-designer/page.tsx`, `BpmnDesigner.tsx`, `DmnDesigner.tsx`. `.badge` and
`.login-form` were dead CSS (confirmed via grep — never referenced in any `.tsx`), dropped outright.

**Deliberately NOT touched**: `FederatedStepPropertiesProvider.tsx`, `SignatureTaskPropertiesProvider.tsx`,
`DmnValidationPropertiesProvider.tsx`. These render through `bpmn-js-properties-panel`'s own Preact tree
(documented in `DmnValidationPropertiesProvider.tsx` itself: hand-authored JSX there compiles through
this app's React runtime and silently fails to render inside Preact's tree — an already-known,
documented constraint from a prior session), calling the library's own exported components as plain
functions. Confirmed via grep that none of the three ever used any of this app's own CSS classes — out
of scope by construction, not an oversight.

**A third instance of the same defect class, this time app-local rather than cross-app**: the new home
tab bar (`page.tsx`) initially used only `border-b-2 border-{accent,transparent}` on the tab
`<button>`s, the same partial-border mistake already fixed twice this rollout (ADR 0220/0221) for other
elements — caught this time via the static computed-style probe itself (`borderLeft: "2px"`, plus a
visible full-box border and a gray default button background in the probe screenshot), before any
Docker rebuild shipped it. Fixed with Tailwind's own documented "border on one side only" pattern:
`border-0 border-b-2 border-{accent,transparent}` (resets all four sides to 0, then re-applies just the
bottom), plus an explicit `bg-transparent` (the original CSS's `background: none` was dropped in the
first pass) — both needed, since an unstyled `<button>` carries a default background as well as a
default border in this environment. Re-verified via the same probe: `borderLeft: "0px"`,
`background: "rgba(0, 0, 0, 0)"`.

## Consequences

- Any future plain `<button>` with only a partial border override (e.g. an active-tab underline) must
  use the `border-0 border-{side}-N` pattern, not a bare `border-{side}-N` alone — this is now the third
  time this exact defect class has appeared in this rollout (ADR 0220: full button border; ADR 0221:
  form-element font; this ADR: button border AND background together for a partial-border case) and is
  worth remembering as this rollout's single most common mistake.
- process-designer's rollout is complete for its entire UI surface except the three Preact-rendered
  properties-panel providers, which remain correctly out of scope.
- Verified via the same `getComputedStyle()` static-probe technique as prior sessions (this app's
  designer/DMN canvases require authentication this session had no credentials for) plus a direct live
  browser check of the (unauthenticated) login page.
