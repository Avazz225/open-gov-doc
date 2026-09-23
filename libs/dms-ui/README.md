# dms-ui

Shared design tokens for all six frontend apps (concept 8, Phase 48 Session 1, [ADR 0168](../../docs/adr/0168-shared-design-tokens-and-scales.md)) — the one deliberate exception to [ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)'s "no shared domain logic between apps" stance (each app's own `auth-context.tsx`, `theme-context.tsx`, etc. stay independently duplicated, unchanged). Everything else about each app stays as it is.

`tokens.css` is a plain CSS file, not an npm package — this repo's six Next.js apps have no existing JS workspace tooling to hook a real package into, and a plain relative `@import` needs none. Consumed by adding a single line at the top of an app's own `src/app/globals.css`:

```css
@import "../../../../libs/dms-ui/tokens.css";
```

(the exact relative depth from `apps/<app>/src/app/globals.css` to this file — verified against a real `next build`, the token actually appears in the compiled CSS output).

Contains: the pre-existing `--dms-*` color tokens (de-drifted to one canonical superset — see the file's own header comment for exactly which apps were missing which token, and one real, still-live high-contrast bug this file fixes as a side effect of adoption) plus spacing/radius/shadow/typography scales.

~~**Not yet consumed by any app**~~ — **consumed by all six apps since Phase 49** (one app per session, per this file's own original plan) — stale note, found and struck during Post-Roadmap Phase 75 Session 1's own research.

## `tailwind-preset.css` (Post-Roadmap Phase 75 Session 1, [ADR 0218](../../docs/adr/0218-tailwind-css-v4-tooling-foundation.md))

A second shared file, same plain-`@import`-no-npm-package convention as `tokens.css` above: a Tailwind v4 `@theme inline` block mapping Tailwind's utility namespaces (`--color-*`, `--font-*`, `--text-*`, `--radius-*`, `--shadow-*`) directly onto the `--dms-*` variables `tokens.css` already defines — `bg-accent`/`text-fg`/`rounded-md`/etc. generate utilities that reference `var(--dms-*)`, not a frozen literal, so they stay theme-reactive exactly like the hand-written CSS classes already are. Imported after `tokens.css` (references its variables):

```css
@import "../../../../libs/dms-ui/tokens.css";
@import "../../../../libs/dms-ui/tailwind-preset.css";
```

Wired into all six apps' build pipelines (`tailwindcss`/`@tailwindcss/postcss`/`postcss` devDependencies, a `postcss.config.mjs`, `@source` scoped to that app's own `src/`) — but **no app has actually replaced any hand-written CSS with Tailwind utilities yet**, and each app's `globals.css` deliberately omits Tailwind's `preflight` base-reset layer for now (it visibly broke existing hand-written UI when tried, see ADR 0218) — that happens app-by-app starting with the P75-S2 login-page pilot, the same "prove tooling first, redesign after" split `tokens.css` itself already used across Phase 48→49.
