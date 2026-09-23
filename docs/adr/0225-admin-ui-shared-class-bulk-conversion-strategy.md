# 0225: admin-ui Rollout Strategy Change — Bulk-Convert Shared Classes Instead of Per-Route Sessions

## Status

Accepted (P75-S8).

## Context

[ADR 0224](0224-admin-ui-tailwind-shell-conversion-dead-banner-css-fix.md) converted admin-ui's shell
layer and left ~15-30-file-shared classes (`.card`, `.data-table`, `.badge`, `.form-grid`,
`.attribute-row`, `.checkbox-group`, `.layout-row`, `.layout-field`, `.deletion-reason-catalog*`,
`.hint`, `.error-text`, `.empty-state`, `.actions`) untouched, with the plan implying each of admin-ui's
remaining ~30 route-specific components would get its own future session (mirroring the small apps'
one-app-per-session pattern).

Starting on the next natural session — the "Installations" sidebar group (`InstallationManager.tsx`,
`FleetManagementView.tsx`) — showed this plan doesn't fit admin-ui's actual structure. Both components'
own CSS classes are **entirely** the shared set above; neither has any page-specific class of its own
left to convert. Checking two of the largest remaining components (`ObjectTypeEditor.tsx`,
`UserManagement.tsx`) confirmed the same pattern. Admin-ui's real architecture is: a small shell (done in
P75-S7) plus a shared "component class" vocabulary (`.card`/`.data-table`/`.badge`/etc.) reused across
nearly every one of its ~30 pages, not 30 pages each with their own bespoke CSS. A "convert one route's
CSS, page by page" session plan makes little sense against that shape — most sessions would find almost
nothing left to convert on their assigned page.

## Decision

**Convert the shared classes themselves, across all their consumers in one pass per class group**,
rather than continuing a per-route session plan. This session (P75-S8) converts the four
single-purpose, always-standalone classes: `.hint`, `.error-text`, `.empty-state`, `.actions`. Usage was
confirmed via grep first — all four appear **only** as `className="<name>"`, never combined with another
class anywhere in the app (32/33/31/19 files respectively) — making a mechanical, safe bulk
find-and-replace viable: `sed` across every consuming file, then delete the four rules from
`globals.css`. Re-verified afterward: `tsc`/`eslint` clean, full Vitest suite still 304/304, production
build succeeds across all 34 routes, and a computed-style probe against the real rebuilt/redeployed
Docker image confirms all four utility strings resolve to the exact same computed values the original
CSS produced (`opacity: 0.8`/`font-size: 13.6px` for `.hint`, danger-red for `.error-text`, italic +
`opacity: 0.7` for `.empty-state`, `display: flex`/`gap: 8px` for `.actions`).

The remaining, more structurally complex shared classes (`.card`, `.data-table`, `.badge`,
`.form-grid`, `.attribute-row`, `.checkbox-group`, `.layout-row`, `.layout-field`,
`.deletion-reason-catalog*` — several combine with modifier classes or have nested-selector rules for
child elements) need per-class, not purely mechanical, handling in following sessions, but the same
"convert the shared class across every consumer at once" strategy applies, not a per-route split.

## Consequences

- Future admin-ui Tailwind sessions should audit a shared class's exact usage pattern (via grep, ideally
  checking for combined-class strings) before deciding whether a bulk mechanical replacement is safe,
  the way this session did — not assume a scope split by route/page.
- `user-ui`, not yet started, should get the same structural check before its own rollout plan is
  written — it may have the same "shell + shared component classes" shape as admin-ui, or it may not
  (its `globals.css` is already known to be much larger, 972 lines, with dockview-specific theming that
  has no admin-ui analog).
