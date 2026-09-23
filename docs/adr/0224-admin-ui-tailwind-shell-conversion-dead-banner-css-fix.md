# 0224: admin-ui Tailwind Rollout — Shell Layer First, Split Across Sessions

## Status

Accepted (P75-S7, first of admin-ui's own multi-session rollout).

## Context

[ADR 0223](0223-reviewer-ui-tailwind-rollout-cases-pane-dead-css-fix.md) completed the small-apps
rollout group. `admin-ui` and `user-ui` are next, each named for "its own dedicated session" in the
phase's plan — but that estimate was made before surveying their actual size. `admin-ui` alone has
~50 components and ~10,800 lines of TSX, roughly 4-5x larger than `process-designer` (the biggest small
app, which still took a full session for ~2,500 lines). Converting the entire app in one session would
not get the same verification rigor every prior session had.

## Decision

**Split admin-ui's rollout across multiple sessions**, the same re-scoping this project already applied
once before (the "small apps" group was originally envisioned as looser, then split into four full
sessions once each app's real size became clear). This session (P75-S7) converts only the **shell/chrome
layer** — the navigation framework every one of the ~30 route pages sits inside: `AdminShell.tsx`,
`AdminSidebar.tsx`, `ThemeSwitcher.tsx`, `LocaleSwitcher.tsx`, `InstallationSwitcher.tsx`,
`MaintenanceBanner.tsx`, `LicenseStatusBanner.tsx`, `RequireAuth.tsx`, `RequireCapability.tsx`,
`DashboardWidgets.tsx`, the home page, and the login page.

**Only classes scoped exclusively to those files were removed from `globals.css`** — `.admin-shell`,
`.admin-body`, `.admin-sidebar`, `.sidebar-*`, `.admin-content`, `.top-bar`, `.top-bar-actions`,
`.installation-switcher`/`.theme-switcher`, `.page` — each confirmed via grep to have no other
consumers before removal. `.entry-list`/`.entry-row`/`.login-form` were dead CSS (confirmed unused
anywhere), dropped outright. Everything else — `.card`, `.data-table`, `.badge`, `.form-grid`,
`.attribute-row`, `.checkbox-group`, `.layout-row`, `.layout-field`, `.deletion-reason-catalog*`,
`.hint`, `.error-text`, `.empty-state`, `.actions` — is used by 15-30+ of this app's other feature
components (confirmed via grep) and was **deliberately left untouched**; converting those belongs to
their own future sessions targeting specific feature areas, not bundled into this one.

**A cascade-layer implication of the partial conversion**: `.card` stays unlayered, shared CSS.
`DashboardWidgets.tsx` needs to cancel `.card`'s own `margin-bottom` inside a CSS grid (where the
grid's own `gap` already spaces rows — the leftover margin would double the spacing). A Tailwind `mb-0`
utility on the same element would lose to `.card`'s unlayered rule regardless of order, per the
cascade-layer bug ADR 0219 already found for office-addin. Kept as one small real CSS rule
(`.dashboard-widget { margin-bottom: 0; }`) instead, positioned after `.card` in file order so it wins
on the same unlayered footing — the correct answer while `.card` itself stays out of scope, not a
regression from the established "convert everything to utilities" pattern.

**Two more dead-CSS-class bugs found, both fixed as a byproduct**: `MaintenanceBanner.tsx`'s
`.maintenance-banner` and `LicenseStatusBanner.tsx`'s `.license-banner` were **both** never defined
anywhere in `globals.css` — this app's two most important persistent warning banners (system-wide
maintenance mode, license problems) have been rendering as plain unstyled text this whole time, easy to
miss. Given real styling now (matching the `bg-danger-bg`/`text-danger` pattern already established for
this exact use case in the small apps).

**A fourth defect found via the same computed-style probing technique, novel this session**: the
sidebar's collapsible group-toggle `<button>` had no explicit text-color utility (the only button this
session missing one — every other button explicitly carries `text-fg`). The probe showed
`color: rgb(0, 0, 0)` (pure black, the browser's UA default button text color) instead of the expected
`rgb(26, 26, 26)` (`--dms-fg`) — invisible in light theme (both are near-black) but would have been a
real, hard-to-notice bug in dark/high-contrast themes (black text on a dark background). Fixed with an
explicit `text-fg`.

## Consequences

- admin-ui's shell now looks and works as intended — two real warning banners visible for the first
  time, a clean top bar, and a collapsible sidebar with a properly highlighted active link.
- admin-ui's remaining ~30 route-specific components/pages need their own future sessions
  (P75-S8 onward), each scoped to one or a few related feature areas, following this session's
  established discipline: confirm a class's usage scope via grep before removing it from the still-
  shared `globals.css`.
- `user-ui` (similarly large, ~10,800 lines, plus dockview-specific theming not present in admin-ui)
  will need the same multi-session treatment, not a single pass.
