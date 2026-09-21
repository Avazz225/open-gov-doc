# 0168 — Shared design tokens (`libs/dms-ui/tokens.css`) and new spacing/radius/shadow/typography scales

**Status:** accepted
**Context:** P48-S1 (Phase 48, "Design System Foundation", the '2026 modern look' initiative). The
existing `--dms-*` color-token convention (ADR 0009) and `data-theme` light/dark/high-contrast
mechanism are consistent in *spirit* across all six frontend apps, but each app's `globals.css`
independently copy-pastes its own copy — a deliberate choice at the time (ADR 0006: no shared code
between frontend apps), but one that has visibly drifted since. This session audited every app's
actual `globals.css` before writing anything down, rather than assuming the drift the plan predicted:

- **Missing color tokens, confirmed per app**: `user-ui` alone has `--dms-accent-bg-strong`,
  `--dms-surface`, `--dms-surface-fg` (added for `dockview` theming, P16-S1) but is missing
  `--dms-hover-bg`, which the other four theme-capable apps all have. `office-addin` has neither
  `--dms-hover-bg`'s siblings nor `--dms-success-bg` at all, and has **no `data-theme` blocks
  whatsoever** (confirmed intentional — it never gained a manual theme switcher, `docs/services/
  office-addin.md`, and light/dark comes only from `@media (prefers-color-scheme)`).
- **A real, currently-live bug found by this audit**: `user-ui`/`admin-ui`/`process-designer`'s
  high-contrast `--dms-accent-bg` is still `#ffff00` — identical to `--dms-accent`, making
  `.badge.pending`-style elements yellow-on-yellow and unreadable. `reviewer-ui`/`migration-console`
  already fixed this exact bug for themselves in Post-Roadmap Phase 33 Session 1 (ADR 0135), but that
  session never touched the other three apps' own copies, so the bug is still live in three of five
  theme-capable apps today.
- **No scale at all beyond color**: `border-radius` alone appears as `4px`, `0.375rem`, `0.25rem`,
  `0.5rem`, `5px`, and `0.4rem` across the six apps' `globals.css` files for what is visually the
  same small handful of shapes (small controls, buttons/cards, panels/modals, pills). `gap`/`padding`
  values cluster loosely around a 4px grid (`0.25/0.5/0.75/1/1.5rem` account for the large majority)
  but with off-grid outliers (`0.15/0.3/0.35/0.4/0.6rem`) mixed in. `font-size` similarly clusters
  around `0.75/0.85/0.9/1/1.1/1.5rem` with no named scale. `box-shadow` is barely used at all (two
  near-identical values found across the entire codebase).

## Decision

**One real shared source**: `libs/dms-ui/tokens.css`, a plain CSS file — not an npm package, not a
new JS workspace. This repo's six Next.js apps have no existing JS monorepo tooling (no root
`package.json`, no `pnpm-workspace.yaml`/`turbo.json`) to hook a real package into, and introducing
one just for this would be a much larger infrastructure change than "one shared source" implies.
Consumed via a single relative `@import` at the very top of an app's own `globals.css`:

```css
@import "../../../../libs/dms-ui/tokens.css";
```

Verified against a real `next build` (`process-designer`, chosen for being the smallest app) before
writing this decision down: the import resolves, the build succeeds, and the token actually appears
in the compiled CSS output. The spike edit was reverted immediately afterward — **no app's own
`globals.css` imports this file yet**; that is Phase 49's job, one app per session, not this one.

**`tokens.css`'s color section is the de-drifted superset** of every app's individual token set,
with the high-contrast `--dms-accent-bg` value fixed to `#000000` (matching ADR 0135's already-fixed
apps) rather than perpetuating the still-live `#ffff00`-on-`#ffff00` bug into the shared file.
Adopting this file in Phase 49 therefore fixes that bug in `user-ui`/`admin-ui`/`process-designer`
as a side effect of the rollout, not a separately tracked fix.

**New scales, grounded in the real values found above, not invented from scratch**:
- **Spacing** (`--dms-space-1` through `--dms-space-8`): a 4px base grid (`0.25/0.5/0.75/1/1.5/2rem`)
  matching the values that already dominate actual usage; the off-grid outliers found in the audit
  are expected to round to the nearest step during Phase 49's rollout, not preserved as their own
  scale steps.
- **Radius** (`--dms-radius-sm/md/lg/full`): four steps matching the four shapes actually in use
  today (`0.25/0.375/0.5rem`, `999px` for pills).
- **Shadow** (`--dms-shadow-sm/lg`): two steps matching the two values already in use, kept
  **theme-invariant** — elevation is barely used in this codebase at all, and inventing per-theme
  shadow variants this project has never needed would be speculative, not grounded in real usage.
- **Typography** (`--dms-font-family`, `--dms-font-family-mono`, `--dms-font-size-xs/sm/base/lg/xl`):
  the font stack itself was already identical across all six apps (`system-ui, -apple-system, "Segoe
  UI", sans-serif`) — tokenizing it removes six identical copies without changing anything visually.
  Font sizes get five named steps matching the values already observed clustering in real usage.

**`office-addin`'s already-intentional reduced theming stays exactly as-is, not force-unified**: no
manual switcher, no `data-theme` blocks, `light`/`dark` only via the OS preference — this was a
deliberate choice (space constraints, "follow the host application" reasoning already established
for both theme and locale, see ADR 0167) and this session does not reverse it. When `office-addin`
eventually adopts `tokens.css` in Phase 49, it will simply never set `data-theme`, relying only on
the file's `:root`/`@media` blocks — no special accommodation was needed in the token file itself for
this to already work correctly.

**Sign-off gate**: a published Artifact previewing the token set, the new scales, and a handful of
representative components rendered in light/dark/high-contrast was shown to the user for explicit
approval before any further session in this initiative proceeds — the right checkpoint for something
this subjective, rather than guessing at "nicer" across six apps sequentially and finding out only at
the end whether the direction landed.

## Rationale

- **Why a plain CSS file instead of the npm-package option the plan itself floated**: the plan's own
  text offered "a new `libs/dms-ui` CSS/token package, or a shared Next.js config layer" as two
  options to choose between. A real npm package needs a workspace root to link into; building that
  root just to carry one CSS file would be new infrastructure disproportionate to the actual need.
  The relative-`@import` mechanism achieves "one real shared source" with zero new tooling, and was
  proven to actually work before being written down as the decision, not merely assumed.
- **Why de-drift into one superset instead of picking one app's existing file as the base
  unmodified**: every app's own file turned out to have at least one gap or bug relative to the
  others (see "Context" above) — copying any single app's file verbatim would have carried that
  app's specific gaps into the new shared source, defeating the point of consolidating in the first
  place.
- **Why fix the high-contrast bug here instead of a separate P44-style bugfix session**: the fix is a
  single value inside the same token file this session was already building for an unrelated reason
  (consolidation) — the same "found live, fixed in the same session" pattern already established
  multiple times in this project (e.g. the Keycloak profile-wipe bug in P47-S1) rather than opening a
  dedicated session for a one-line value correction.
- **Why the new scales are grounded in actually-observed values instead of a textbook 8-point grid or
  similar**: this project's own existing values already cluster close to a 4px grid with real-world
  outliers; picking scale steps that match what's already mostly true (and rounding the exceptions
  during rollout) produces a smaller visual diff during Phase 49 than replacing everything with an
  unrelated numbering scheme would.
- **Why shadows stay theme-invariant rather than gaining light/dark-specific variants**: with only two
  shadow values found in active use across the entire codebase, inventing per-theme elevation
  variants now would be speculative design work with no real precedent to base it on — a case where
  "match reality" means recognizing reality doesn't need this yet, not filling in a scale for its own
  sake.
- **Why no app is touched this session**: the plan's own text is explicit — "get explicit user
  sign-off before any app is touched" and "before it's rolled out anywhere." The one exception (the
  `process-designer` spike used to prove the `@import` mechanism works) was reverted immediately
  after the build succeeded, leaving zero net change to any app.

## Consequences

- ~~**`libs/dms-ui/tokens.css`** exists and is documented (`libs/dms-ui/README.md`, `libs/README.md`
  updated to note this one non-Python exception), but is consumed by **no app yet**.~~ — **closed by
  Phase 49's rollout** (Sessions 1-3): all six frontend apps (`admin-ui`, `user-ui`, `reviewer-ui`,
  `process-designer`, `migration-console`, `office-addin`) now `@import "../../../../libs/dms-ui/
  tokens.css"` at the top of their own `globals.css`.
- ~~**Phase 49** (visual modernization rollout) will, per app: add the `@import` line, remove that
  app's own now-duplicate `--dms-*` declarations from its `globals.css`, and replace hardcoded
  radius/spacing/shadow/font-size values with the new scale tokens where they match (rounding
  off-grid outliers to the nearest step) — real browser verification (before/after screenshots, both
  themes) per the project's own established convention for every UI-visible change.~~ — **done**, see
  above.
- ~~**The live high-contrast bug is fixed for `user-ui`/`admin-ui`/`process-designer`** the moment each
  adopts `tokens.css` in Phase 49 — not before, since no app has been touched yet.~~ — **done**, see above.
- **`office-addin`'s reduced theming is now an explicitly recorded exception** (this ADR), not
  something a future session might "notice" is inconsistent and try to unify away.
- **No new JS tooling, dependency, or workspace configuration was added** to the repository — the
  `@import` mechanism works with each app's existing, independent Next.js build exactly as before.
