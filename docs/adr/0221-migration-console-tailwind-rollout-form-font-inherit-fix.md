# 0221: migration-console Tailwind Rollout, and a Retroactive Form-Element Font Fix Across Five Apps

## Status

Accepted (P75-S3 continuation, second of the small-apps rollout group).

## Context

[ADR 0220](0220-office-addin-tailwind-rollout-primary-button-border-fix.md) converted `office-addin`
in full and, via computed-style probing, caught a retroactive primary-button border defect across all
six apps. This session continues the small-apps rollout with `migration-console`, and the same
probing discipline caught a second retroactive defect from P75-S2, this time in **five** of the six
apps.

## Decision

**migration-console: full conversion.** Every remaining hand-written CSS class (`.page`/`.top-bar`/
`.top-bar-actions`/`.tab-nav`/`.hint`/`.actions`/`.login-form`/`.error-text`/`.success-text`/
`.empty-state`/`.data-table`/`.badge*`/`.maintenance-banner`/`.inline-form`/`.detail-row`) converted to
Tailwind utilities directly on `Shell.tsx`, `ThemeSwitcher.tsx`, `LocaleSwitcher.tsx`,
`MaintenanceBanner.tsx`, `RequireAuth.tsx`, `PairedInstallationList.tsx`, `TransferConsole.tsx`. Since
this app previously had **no styling at all** on most of its plain `<button>`/`<select>` elements
(unlike office-addin, which at least had a generic `button {...}` rule), this session also gave them a
real visual treatment for the first time — the same secondary/primary button and form-field language
already established on the login page and office-addin's rollout, not a mechanical "port a rule that
didn't exist" no-op.

One exception was kept as dedicated CSS rather than forced into a Tailwind utility: the high-contrast
badge border (ADR 0119/0135) needs an ancestor `[data-theme="high-contrast"]` selector, which has no
clean Tailwind utility expression in this app's setup. Kept as a small `@layer base` rule
(`:root[data-theme="high-contrast"] .badge-hc-border { border: 1px solid currentColor; }`), applied
alongside the Tailwind badge utility classes — a deliberate, narrow exception, not a retreat from the
rollout.

**A second retroactive defect found via computed-style probing**: `getComputedStyle()` on
migration-console's login input showed `font-family: Arial` instead of the app's actual font stack
(`system-ui, -apple-system, "Segoe UI", sans-serif`). Checking the same property across **all five**
non-office-addin apps' login pages showed the identical mismatch in every one — none of them had a
genuinely global `input, select, button, textarea { font: inherit }` reset (the kind `preflight`
normally provides, deliberately excluded per ADR 0218); each app's existing `font: inherit` rules, where
they existed at all, were scoped to specific pre-existing classes, not broad enough to cover the new
Tailwind-styled login inputs from P75-S2. Fixed by adding this reset to all five apps' `globals.css`,
layered in `@layer base` (matching ADR 0219's cascade-ordering precedent, so a future Tailwind
typography utility on a form element still wins) — for `user-ui`/`admin-ui`/`reviewer-ui`/
`process-designer`, this is a small, surgical addition; those four apps' own full rollout (replacing the
rest of their hand-written CSS) is explicitly still deferred to their own dedicated future sessions per
the phase's plan, not attempted here. Re-verified via `getComputedStyle()` against the real running
Docker containers for all five apps: input font now matches body font in every case.

## Consequences

- Any app not yet in this rollout must still carry this minimal `font: inherit` reset once Tailwind
  utilities are used on its form elements — the four remaining apps (`user-ui`, `admin-ui`,
  `reviewer-ui`, `process-designer`) already have it from this session; any new app added to the repo
  would need it added deliberately, not assumed.
- Two retroactive defects (ADR 0220's button border, this ADR's form-element font) were both found only
  by directly inspecting computed styles, not by screenshot review alone — reinforces that every future
  P75-S3+ session should keep using this technique on every new interactive element it introduces.
- migration-console's rollout is now complete for its entire UI surface — no hand-written CSS classes
  remain in its `globals.css` beyond the minimal reset and the one documented high-contrast exception.
