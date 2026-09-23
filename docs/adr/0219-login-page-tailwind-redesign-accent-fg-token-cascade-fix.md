# 0219: Login Page Tailwind Redesign — `--dms-accent-fg` Token and office-addin `@layer base` Cascade Fix

## Status

Accepted (P75-S2).

## Context

[ADR 0218](0218-tailwind-css-v4-tooling-foundation.md) (P75-S1) wired Tailwind CSS v4 into all six web
apps but made no visual changes. P75-S2 is the pilot rollout: rebuild the login page — small,
self-contained, near-identical across every app, and the user's own specifically-named "clunky, dated"
pain point — as a real visual redesign using the new Tailwind utilities, proving the tooling before the
much larger per-app rollout in P75-S3 onward.

## Decision

**Redesign**: replace the bare `<main>`/`<form>` layout with a centered card (`rounded-lg border
border-border bg-surface shadow-lg`, `max-w-sm` on the five full apps, a narrower unconstrained variant
for `office-addin` given its panel-embedded context) containing a heading, labeled inputs with visible
focus rings, an error alert, and a full-width primary button. Applied identically to `user-ui`,
`admin-ui`, `reviewer-ui`, `process-designer`, `migration-console`; `office-addin` gets a scaled-down
variant (smaller padding/type scale, no `useBranding` since that app has none). `box-border` is added
explicitly to every element combining `width`/`max-width` with `padding`/`border`, since preflight (and
its `box-sizing: border-box` reset) stays excluded per ADR 0218.

**New design-system token — `--dms-accent-fg`**: found live via screenshot that hardcoded `text-white`
on the new `bg-accent` button fails WCAG AA in two of the three themes — white-on-`#ffff00`
(high-contrast) is barely legible, and white-on-`#60a5fa` (dark theme's accent) computes to ~2.5:1,
well under the 4.5:1 AA threshold for normal text. Rather than a one-off fix on this one button, added a
proper token to `libs/dms-ui/tokens.css` (light `#ffffff`, dark `#0b1220`, high-contrast `#000000`,
each value chosen from an actual relative-luminance contrast computation against that theme's
`--dms-accent`) and mapped it into `libs/dms-ui/tailwind-preset.css` as `--color-accent-fg`. This is a
permanent addition to the shared token set (ADR 0168), available to any future accent-colored control,
not scoped to the login page.

**office-addin cascade-layer bug**: `office-addin`'s pre-existing hand-written `globals.css` has real
global `input[type="password"]`/`button` tag-selector rules, left unlayered when P75-S1 added Tailwind's
`@layer theme, base, components, utilities;` declaration. Per the CSS cascade, unlayered rules always
beat `@layer`-wrapped rules regardless of source order or specificity — so those old rules silently
overrode the new `@layer utilities` Tailwind classes on the login form, invisible in `tsc`/`eslint`/
`vitest` and only found by inspecting actual computed styles. Fixed by wrapping ALL of office-addin's
pre-existing CSS in `@layer base { ... }` (declared before `utilities` in the layer order, so utilities
correctly wins). This is the only one of the six apps affected — it's also the only app with real
global tag-selector rules predating Tailwind; the other five only had descendant/class selectors, which
don't hit this cascade trap.

## Consequences

- `--dms-accent-fg` must be used (not a hardcoded `text-white`/`text-black`) anywhere `bg-accent` is used
  as a button/badge background, in every future session touching an accent-colored control.
- Any future app added to this repo with its own pre-existing global tag-selector CSS must apply the
  same `@layer base` wrapping before adopting Tailwind utilities, or repeat this exact bug silently.
- `office-addin`'s Office.js host-gating (documented in `docs/services/office-addin.md`, P49-S1) blocks
  testing this app's real login flow in a plain browser; verification instead used a standalone static
  probe against the compiled CSS (`getComputedStyle()` on bare elements carrying the real classNames,
  served via the static export) rather than the running React app. This technique is worth reusing for
  any future office-addin CSS verification, since the Playwright `page.route()` office.js-blocking
  workaround only reaches `OfficeGate`'s error screen, not the gated content behind it.
