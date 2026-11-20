# 0238 — Narrow-width patterns for all six apps

**Status:** accepted
**Context:** P83-S9 (Phase 83). [ADR 0237](0237-stageshow-adoption-illustrations-and-craft-lens.md)
Decision 6 records the user's decision (2026-11-20) that the back-office apps should also work on narrow
screens. This ADR is the assessment it asked for: every app's main views were screenshotted at 390 × 844
and 768 × 1024 against the running stack, logged in as `users-admin`, and the page's horizontal overflow
was measured (`document.documentElement.scrollWidth` against the viewport width). It fixes the patterns
once, for all apps, and the sessions that implement them.

## Findings

34 views measured; 22 scroll the whole page sideways.

| App | 390 px | 768 px | What breaks |
|---|---|---|---|
| `user-ui` | no overflow, unusable | usable | Dockview keeps three panes side by side: at 390 the explorer is ~170 px wide and names break into three lines. Header wraps the user name above its button. |
| `admin-ui` | 7/7 overflow (509–983 px) | 3/7 overflow | Sidebar keeps its 245 px, content gets ~145 px; launcher cards and forms run off the edge. Wide tables (users, registry, reports) overflow at 768 too. Two-column form grids don't collapse. |
| `process-designer` | list overflows (948 px) | list overflows | The definitions table pushes the page. In the designer the 300 px properties panel leaves the canvas ~70 px. |
| `migration-console` | 2/2 overflow (~740 px) | ok | Header cluster (language, theme, user, log out) sits on the same row as the tabs and runs off. Transfer table cut. |
| `reviewer-ui` | 4/4 overflow (874–1112 px) | 4/4 overflow | Same header row problem, even at 768. Task and approval tables with row buttons overflow at both widths. |
| `office-addin` | ok | ok | Built for Word's task pane; nothing to do. |
| Logins (all) | ok | ok | The card stands alone and fits. |

Two things that are not width problems were seen on the way and are recorded for the matching session:
reviewer-ui's approval inbox shows raw action types (`auth.ad_group_mapping.default_role_set`) as content
— punch-list item 6, missed in P83-S7 — and its task list shows the related object as a full UUID.

## Decision 1 — One breakpoint pair, Tailwind's defaults

- **Narrow: below 768 px** (`md`). Phone portrait and the smallest tablets. All structural changes below
  (drawer, single pane, account menu, stacked rows) switch here.
- **Medium: 768–1023 px** (`md`–`lg`). The desktop layout stays; only overflow is contained (Decision 4).
- **Wide: from 1024 px.** Unchanged.
- Container queries, not viewport media queries, where a component lives in a resizable pane (user-ui's
  dockview panes already gate the empty-state stills this way).

No custom breakpoints: the apps already use Tailwind v4 with the shared preset (ADR 0218), and the default
scale fits the measured break points.

## Decision 2 — The header cluster becomes an account menu below 768 px

Every app with the shared header cluster (language, theme, user, log out; P83-S7 item 10) shows it as a
single account button (the user's name, or an icon with the name as accessible label) below 768 px. It
opens a popover with the same four controls, stacked, each ≥ 40 px tall. Wide layouts keep the inline
cluster.

*Refinement (P83-S11):* where the inline cluster and the tabs don't fit one row at 768px, the switch
moves to 1024px (`AccountMenu inlineFrom="lg"`). reviewer-ui is the one case: four tabs plus the
cluster overflowed at 768px.

Navigation tabs (reviewer-ui, migration-console, process-designer) get the row to themselves; if they
still don't fit they scroll horizontally inside their own strip with an edge fade, never wrapping into a
second row and never scrolling the page.

## Decision 3 — Navigation: admin-ui's sidebar becomes a drawer; user-ui keeps its rail

- **admin-ui** below 768 px: the sidebar is hidden and opens as a modal drawer from a menu button in the
  header (focus trapped, closes on navigation, Escape and backdrop). The home launcher (P83-S7 item 7)
  is the landing page's navigation in that state, as ADR 0237 Decision 6 anticipated. From 768 px the
  sidebar stays as it is.
- **user-ui** keeps its 48 px icon rail at every width. It fits at 390 (12 icons, vertical, scrolls if the
  screen is short), and a bottom tab bar cannot hold 12 views. No hamburger.

## Decision 4 — Tables: contained scroll by default, stacked rows where the phone task is the row

- **Default, every app:** every data table sits in a horizontal scroll container with an edge fade on the
  side that has more. The page itself never scrolls sideways at any width. One shared CSS class in
  `libs/dms-ui` (plain CSS, same convention as `tokens.css`; no shared JS per ADR 0006), so all apps use
  the same container.
- **Stacked rows below 768 px** only where acting on a single row is the job on a phone:
  reviewer-ui's tasks and four-eyes approvals, user-ui's approval inbox (already a list). Each row becomes a
  card: title line, two or three secondary facts, its actions as full-width buttons at the bottom.
- admin-ui's tables stay scroll containers: configuration on a phone is an exception, and stacking 24
  different tables would be 24 bespoke layouts for little use.

## Decision 5 — user-ui's dockview: single-pane mode below 768 px

Below 768 px the three-pane dockview layout is replaced by one pane at a time with a segmented switcher
(Explorer · Vorschau · Metadaten) under the header. Opening a document switches to Vorschau; a back
control returns to Explorer. The user's saved dockview layout is not touched, so returning to a wide
window restores it. "Standardanordnung" is hidden in single-pane mode (there is nothing to arrange).

## Decision 6 — process-designer: list works narrow, modelling stays a wide-screen task

The definition lists get Decision 4's scroll container. The designer below 768 px hides the properties
panel behind a toggle (an overlay sheet from the right), so the canvas gets the width. Authoring BPMN on a
phone is not a goal; viewing and small edits are.

## Decision 7 — Forms collapse to one column; touch targets ≥ 40 px narrow

Two-column form grids become one column below 640 px (`sm`), inputs fill the width. Below 768 px every
interactive control in rows, menus and the account popover is at least 40 px tall (the launcher rows
already are).

## Decision 8 — No compact login illustration

ADR 0237 asked whether narrow logins need a `compact` illustration. No: the logins render the card alone
below 1200 px and it fits cleanly at 390 (checked for all five). On a phone the login is a screen people
pass through in seconds; a second illustration design per app would cost five compact designs, their
rehearsals and bundle weight, for no task. The ≥ 1200 px rule stays. The empty-state stills keep their
pane-width gate, which already handles narrow panes.

## Consequences

Implementation sessions (each with live screenshots at 390/768/1400 px before and after, and the overflow
measurement re-run with no page overflow left in the app):

1. **P83-S10 — Shared foundation + the two small apps:** the scroll-container class in `libs/dms-ui`;
   the account-menu header (Decision 2) and tab strip in `migration-console` and `process-designer`;
   process-designer's properties-panel toggle (Decision 6).
2. **P83-S11 — `reviewer-ui`:** account menu, stacked task/approval rows (Decision 4), plus the two
   findings above (translated action types, a short related-object id).
3. **P83-S12 — `admin-ui`:** sidebar drawer (Decision 3), account menu, scroll containers on all 24
   tables, one-column forms (Decision 7).
4. **P83-S13 — `user-ui`:** single-pane mode (Decision 5), header at narrow widths, stacked approval rows.
5. **P83-S14 — Docs/README hero + phase close** (the former "last session").

`office-addin` needs no session. E2E specs run at desktop size and are unaffected; each session adds one
narrow-viewport e2e check that the page does not overflow.
