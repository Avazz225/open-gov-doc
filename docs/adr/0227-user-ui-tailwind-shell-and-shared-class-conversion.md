# 0227: user-ui Tailwind Rollout — Shell + Broadly-Shared Classes, Three Real Bugs Found and Fixed

## Status

Accepted (P75-S10, first session of user-ui's rollout).

## Context

With admin-ui's rollout declared functionally complete (ADR 0226), this session starts `user-ui` — the
last app in Phase 75. A structural survey (same lens as ADR 0225/0226) found `user-ui` has a genuinely
different shape than admin-ui: not ~30 thin routes over a shared component-class vocabulary, but a
single-page dockview-based workspace (990-line `globals.css`, ~65 distinct classes, many single-file
feature-specific — redaction, OCR overlay, splitter, breadcrumbs, dockview theme-variable mapping — not
a small shared set reused everywhere).

## Decision

**This session converts two categories, mirroring admin-ui's P75-S7/S8 split**: the shell/chrome layer
(`DocumentWorkspace.tsx`'s workspace/top-bar/workspace-body wrapper, `IconRail.tsx`, `ContextMenu.tsx`,
`ThemeSwitcher.tsx`, `LocaleSwitcher.tsx`, `RequireAuth.tsx`, `MaintenanceBanner.tsx`, the login/
callback/share pages), and every single-element shared class confirmed via grep to be used only
standalone or with dead modifier classes (`.hint`, `.error-text`, `.empty-state`, `.actions`,
`.pane-heading` — 18-29 files each). `.login-form` was already dead CSS (0 usages, login page already
redesigned in P75-S2). The remaining shared classes (`.badge` + modifiers, `.entry-row`/`.tree-row`
patterns, dockview `--dv-*` theming, splitter, breadcrumbs, `.layout-grid*`, `.share-card`/
`.share-download-button`, redaction/OCR-specific classes) stay as permanent, deliberately-kept shared
component classes — the same reasoning as ADR 0226, not re-litigated here.

**Three real, pre-existing dead-CSS-class bugs found and fixed as a byproduct**: `.maintenance-banner`
(same bug as admin-ui's ADR 0224, independently present here too — this app's maintenance-mode banner
has been rendering unstyled), and two hint modifier classes that never added anything
(`hint accessibility-warning` in `PreviewPane.tsx`, `hint search-syntax-hint` in `SearchPane.tsx`) —
both collapsed into the same real `.hint` styling as every other hint in the app, since their modifier
names never carried any actual CSS to preserve. A fourth, `.signature-actions` in `SignaturesPanel.tsx`
(never defined, found while auditing `.actions`), got real `flex items-center gap-2` styling.

**Two real regressions caught before shipping, via the same techniques prior sessions established**:

1. **Cascade conflict, `.pane-heading`'s bulk conversion vs. `.modal-header .pane-heading`'s override**:
   `.modal-header .pane-heading { margin: 0; }` canceled the base class's `margin-bottom` for headings
   inside a modal dialog. The bulk `.pane-heading` → `m-0 mb-3 text-base` replacement gave all 6 modal
   headings **both** `m-0` and `mb-3` simultaneously — a same-layer, same-property utility conflict with
   no guaranteed winner (the exact class of bug ADR 0219 first identified, here between two Tailwind
   utilities rather than a utility vs. unlayered CSS). Fixed by checking each of the 6 usages
   individually and removing the incorrect `mb-3`, leaving just `m-0` — verified by construction (no
   conflicting margin utility present at all), not by chance ordering.
2. **The same class of conflict, freshly introduced in `IconRail.tsx`'s own conversion**: the button
   template literal put `bg-transparent` in the shared base class string and appended `bg-accent-bg` for
   the active state — meaning active buttons carried both. A computed-style probe against the real
   compiled CSS confirmed `bg-transparent` was winning (`background-color: rgba(0,0,0,0)` even on the
   "active" button) — the icon rail's active-view highlight was silently broken. Fixed by making the
   base class carry neither, and the ternary supply exactly one of `"bg-accent-bg"` /
   `"bg-transparent"` per button, never both — re-verified via the same probe
   (`rgba(37, 99, 235, 0.15)` on active, `rgba(0, 0, 0, 0)` on inactive) and a full screenshot.

## Consequences

- Any future component that conditionally toggles a Tailwind utility must ensure the "off" state and
  "on" state never both supply a value for the same CSS property in the same class string — this is now
  the second time this exact mistake has occurred in this rollout (ADR 0226's modal headings, this
  session's icon rail), worth treating as a standing checklist item, not a one-off.
- `user-ui`'s remaining hand-written CSS (dockview theming, redaction/OCR-specific classes, entry-row/
  tree-row patterns, `.badge` + modifiers, `.share-card`/`.share-download-button`, `.layout-grid*`)
  needs its own future session(s) if further conversion is wanted, following the same "check real usage
  scope before converting" discipline — not attempted this session.
- `.share-card`/`.share-download-button`/`.share-link-url` (shared between `share/page.tsx` and
  `ShareLinkModal.tsx`, 2 files) were deliberately left as-is this session — small enough scope for a
  quick future pass, not urgent.
