# 0223: reviewer-ui Tailwind Rollout, Completing the Small-Apps Group

## Status

Accepted (P75-S6, fourth and last of the small-apps rollout group).

## Context

[ADR 0222](0222-process-designer-tailwind-rollout-tab-button-default-styling-fix.md) converted
`process-designer`. This session converts `reviewer-ui`, the last of the four small apps grouped
together for this phase (`office-addin`, `migration-console`, `process-designer`, `reviewer-ui`) —
`admin-ui`/`user-ui` each get their own dedicated session next, per the phase's own plan.

## Decision

**reviewer-ui: full conversion.** Every remaining hand-written CSS class (`.page`/`.top-bar`/
`.top-bar-actions`/`.tab-nav`/`.hint`/`.actions`/`.login-form`/`.error-text`/`.success-text`/
`.empty-state`/`.detail-fields`/`.data-table`/`.badge*`/`.maintenance-banner`/`.inline-form`/
`.detail-row`) converted to Tailwind utilities directly on `Shell.tsx`, `ThemeSwitcher.tsx`,
`LocaleSwitcher.tsx`, `MaintenanceBanner.tsx`, `RequireAuth.tsx`, `TaskList.tsx`, `ApprovalList.tsx`,
`TeamTaskList.tsx`, `InstanceDetail.tsx`, `CasesPane.tsx`. Same high-contrast badge-border exception as
`migration-console`/`reviewer-ui`'s own prior sessions: kept as a small `@layer base` rule
(`.badge-hc-border`), no clean Tailwind utility expression for an ancestor `[data-theme]` selector.

**A real, pre-existing bug fixed as a byproduct, not introduced by this session**: `CasesPane.tsx` (this
app's case-browsing UI, added Post-Roadmap Phase 74 Session 1) referenced three CSS classes —
`.cases-pane`, `.pane-heading`, `.entry-name`, `.entry-meta` — that were **never actually defined
anywhere** in this app's `globals.css` (confirmed via grep before touching anything). This means
`CasesPane` has been rendering completely unstyled since it was added, relying only on browser
defaults and the (correctly defined) `.entry-list`/`.entry-row` classes wrapping its list items. Given
real, intended styling now as part of this conversion (matching the pattern `user-ui`'s own working
`.pane-heading` establishes: `text-base`, tightened margin), rather than preserved as its accidentally-
unstyled current state — consistent with this rollout's established practice of giving previously-
unstyled elements real treatment (ADR 0221's migration-console buttons, this session's own consistency).
`ApprovalList.tsx`'s reject-reason form similarly referenced a never-defined `.form-grid` — replaced
with the same `inline-form`-style treatment used for every other small form in this app.

**No new instance of the button-border/background defect class this session** (ADR 0220/0221/0222 each
found one) — every new interactive element here used explicit `border`/`bg-*` utilities from the start,
informed by the prior three sessions' findings, and the computed-style probe confirmed no stray browser
defaults leaked through.

## Consequences

- The small-apps Tailwind rollout group (`office-addin`, `migration-console`, `process-designer`,
  `reviewer-ui`) is now complete. `admin-ui`/`user-ui` remain, each sized for its own dedicated session
  per the phase's plan (larger component counts, `user-ui` specifically has a much larger `globals.css`
  with dockview-specific theming not present in any small app).
- Any future component discovered to reference an undefined CSS class (like `CasesPane`'s three) should
  get real Tailwind styling matching this app's established visual language, not just be left unstyled
  "to preserve current behavior" — the current behavior in such a case was itself an unintended bug.
