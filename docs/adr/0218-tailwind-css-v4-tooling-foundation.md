# 0218 — Tailwind CSS v4 tooling foundation: theme mapping, shared preset, preflight deferred

**Status:** accepted
**Context:** P75-S1 (Phase 75, first session of the Tailwind CSS migration — reordered ahead of the
original Phase 75 "Beyond the Original Concept" bundle at the user's explicit request after Phase 74
closed, so this is now the active phase). `IMPLEMENTATION_PLAN.md`'s own framing for this session:
decide how Tailwind consumes the EXISTING `--dms-*` token system (`libs/dms-ui/tokens.css`, ADR 0168)
rather than introducing a second, disconnected token system, build a shared preset, and verify the whole
pipeline against a real `next build` in each app's actual static-export mode — tooling only, no visual
redesign yet (that is P75-S2's job).

## Decision

**Tailwind CSS v4** (`4.3.3` as of this session), using its CSS-first `@theme` configuration rather than
a JS `tailwind.config.js` — the natural fit given `--dms-*` tokens already exist as real CSS custom
properties, not something needing translation into a separate JS object.

1. **Shared theme mapping**: new `libs/dms-ui/tailwind-preset.css`, a single `@theme inline { ... }`
   block mapping Tailwind's utility namespaces (`--color-*`, `--font-*`, `--text-*`, `--radius-*`,
   `--shadow-*`) directly onto the existing `--dms-*` variables — `--color-accent: var(--dms-accent)`,
   not a copied literal. The `inline` keyword is required for this to resolve correctly at the point of
   use rather than failing silently in nested DOM contexts (confirmed against Tailwind's own docs, not
   assumed). Consumed the same way `tokens.css` already is: a plain relative `@import` in each app's own
   `globals.css`, no npm package — same "no JS monorepo tooling to hook a real package into" reasoning
   ADR 0168 already established, unchanged by this session. **Deliberately does not override Tailwind's
   default spacing scale**: `--dms-space-1` through `--dms-space-8` already match Tailwind v4's own
   default 0.25rem-per-step spacing unit exactly (both a 4px base grid), so an override would be pure
   duplication with a real drift risk. Font-size steps ARE mapped, since Tailwind's default type scale
   does not coincide with dms-ui's.
2. **Per-app wiring**: `tailwindcss` + `@tailwindcss/postcss` + `postcss` as devDependencies, a
   `postcss.config.mjs` (`{ plugins: { "@tailwindcss/postcss": {} } }`), and three new lines at the top
   of each app's own `globals.css` (see point 3 for why it's three imports, not the simpler
   `@import "tailwindcss";` the framework guide shows for a fresh project).
3. **Source detection explicitly scoped per app, not left to Tailwind's default heuristics**: this repo
   has no npm workspace — six sibling apps under `apps/`, each an independent npm project, with `.gitignore`
   only at the repo root (no per-app boundary Tailwind's default "stop at `.gitignore`" heuristic could
   rely on). `@import "tailwindcss" source(none);` followed by an explicit `@source "../../src";` (allow-
   list, not deny-list) restricts each app's own build to scanning only its own `src/` directory — verified
   this was a real risk, not a theoretical one, via Tailwind's own documentation before writing this
   decision down, rather than assumed safe by default.
4. **Preflight (Tailwind's base CSS reset) is deliberately NOT imported in this session** — found live,
   not merely anticipated: importing the bundled `@import "tailwindcss";` (which always includes
   `preflight.css`) visibly broke `user-ui`'s existing hand-written login page the moment it was enabled,
   confirmed via a real before/after browser screenshot comparison — input fields and the submit button
   lost their visible borders, the heading lost its bold weight, because that page's CSS relies on browser
   *default* styling in places no component has been touched yet to use Tailwind utilities for. Fixed by
   importing the `theme`/`utilities` layers individually (`@import "tailwindcss/theme.css" layer(theme);`
   `@import "tailwindcss/utilities.css" layer(utilities) source(none);`) instead of the bundled import,
   omitting the `preflight.css` layer — Tailwind's own documented mechanism for exactly this situation
   ("integrating Tailwind into existing projects where you want to maintain your own base styles instead
   of Preflight's opinionated resets"). Re-verified via the same before/after screenshot: pixel-identical
   to the pre-Tailwind baseline once preflight was excluded. This exclusion is **temporary and per-app**,
   to be revisited once each app's own P75-S2+ rollout session has actually replaced that app's
   hand-written CSS with Tailwind utilities — preflight can be safely re-added once nothing depends on
   the browser defaults it removes.

## Rationale

- **Why v4's CSS-first `@theme` over a `tailwind.config.js`**: v3's JS config would need every `--dms-*`
  value either hardcoded a second time (immediate drift risk between two token sources) or imported via a
  build-time JS/CSS bridge that doesn't exist in this project's tooling — v4's `@theme inline` maps
  directly onto the CSS variables that already exist, zero duplication, and stays theme-reactive (a
  `data-theme` flip still repaints every Tailwind utility, exactly like it already repaints the
  hand-written CSS today) because the generated utility still contains `var(--dms-*)`, not a resolved
  literal frozen at build time.
- **Why a shared `libs/dms-ui/tailwind-preset.css` file rather than six independently-copied theme
  blocks**: the same "one real shared source for this one layer" precedent Phase 48 (ADR 0168) already
  established for `tokens.css` itself — six independently-maintained copies of the same mapping is exactly
  the drift risk that file was created to avoid in the first place, and there is no reason a second,
  parallel shared file should reopen it.
- **Why preflight is excluded now instead of just accepting the visual break until P75-S2**: this
  session's own named scope is "tooling foundation," explicitly not a redesign — `IMPLEMENTATION_PLAN.md`
  reserves the deliberate, screenshot-verified visual redesign for P75-S2 (the login-page pilot) and
  onward. Silently shipping a broken login page as a side effect of a "just wire up the build tool" session
  would pre-empt that later session's own actual design decision with an accidental one, and would leave
  every app's production build visibly regressed between this session and whenever its own rollout session
  happens — unacceptable for a tooling-only step. Tailwind's own documentation already names this exact
  scenario and provides the mechanism; using it is not a workaround, it's the intended per-project on-ramp.
- **Why explicit `@source` scoping instead of trusting Tailwind's default detection**: verified via
  Tailwind's own documentation that default detection starts from the current working directory and is
  not guaranteed to respect a monorepo boundary that isn't marked by a `.gitignore` at that exact level —
  this repo's apps are siblings under `apps/` with only a root-level `.gitignore`, precisely the case the
  docs flag as needing explicit scoping. Confirmed as the correct fix, not just plausible, before writing
  it into six files.

## Consequences

- **Live-verified**: all six apps (`user-ui`, `admin-ui`, `reviewer-ui`, `process-designer`,
  `migration-console`, `office-addin`) build cleanly in their real `output: "export"` static-export mode
  with the new pipeline wired in, confirmed via `npm run build` for each, not assumed from one app's
  success. `tsc --noEmit`/`eslint` clean across all six. Full Vitest suite green across all six (`user-ui`
  292/292, `admin-ui` 304/304, `reviewer-ui` 55/55, `process-designer` 55/55, `migration-console` 19/20 —
  the one failure is a pre-existing, unrelated flaky `waitFor` timing assertion in
  `transfer-console.test.tsx`, confirmed via `git stash` comparison to fail identically on the unmodified
  code, not caused by this session — `office-addin` 21/21). Generated-CSS correctness confirmed directly:
  a temporary probe (`bg-accent text-fg rounded-md shadow-sm`, added to `user-ui`'s home page, rebuilt,
  inspected in the compiled output, then reverted before commit) produced
  `.bg-accent{background-color:var(--dms-accent)}` in the real build output — proof the mapping resolves
  to the live variable, not a frozen value.
- **Every app now carries `tailwindcss`/`@tailwindcss/postcss`/`postcss` as devDependencies and a
  `postcss.config.mjs`** — no other build configuration changed; Next.js's existing PostCSS pipeline picks
  this up automatically, confirmed live (no webpack override needed in any of the six `next.config.*`
  files).
- **No visual change in this session** — by design. Screenshots of `user-ui`'s and `admin-ui`'s login
  pages confirm pixel-identical rendering to the pre-Tailwind baseline. The actual redesign this migration
  exists to deliver has not happened yet; this ADR only closes the tooling question.
- **Preflight exclusion is a per-app, per-`globals.css` decision, not a permanent project-wide stance** —
  each app's own future rollout session should remove its `layer(base)`/preflight-omission the same
  session it finishes replacing that app's hand-written CSS with Tailwind utilities, not carry the
  exclusion forward indefinitely. Left as an explicit code comment in every `globals.css` this session
  touched, naming exactly when to revisit it, so a future session doesn't have to rediscover this finding.
- **`npm audit` reports pre-existing vulnerability counts** in each app's devDependency tree after this
  install (moderate/high/critical, all in transitive build-tooling dependencies) — not investigated
  further this session; this project's own established convention doesn't gate on `npm audit` output, and
  these are devDependencies (build-time only, never shipped to the static export), not runtime
  dependencies.
