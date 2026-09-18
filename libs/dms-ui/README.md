# dms-ui

Shared design tokens for all six frontend apps (concept 8, Phase 48 Session 1, [ADR 0168](../../docs/adr/0168-shared-design-tokens-and-scales.md)) — the one deliberate exception to [ADR 0006](../../docs/adr/0006-user-ui-static-export-spa.md)'s "no shared domain logic between apps" stance (each app's own `auth-context.tsx`, `theme-context.tsx`, etc. stay independently duplicated, unchanged). Everything else about each app stays as it is.

`tokens.css` is a plain CSS file, not an npm package — this repo's six Next.js apps have no existing JS workspace tooling to hook a real package into, and a plain relative `@import` needs none. Consumed by adding a single line at the top of an app's own `src/app/globals.css`:

```css
@import "../../../../libs/dms-ui/tokens.css";
```

(the exact relative depth from `apps/<app>/src/app/globals.css` to this file — verified against a real `next build`, the token actually appears in the compiled CSS output).

Contains: the pre-existing `--dms-*` color tokens (de-drifted to one canonical superset — see the file's own header comment for exactly which apps were missing which token, and one real, still-live high-contrast bug this file fixes as a side effect of adoption) plus new spacing/radius/shadow/typography scales, none of which existed anywhere before this session.

**Not yet consumed by any app** — Phase 48 Session 1 built and got sign-off on this file only; wiring each app's `globals.css` to import it (and updating that app's own hardcoded values to reference the new scales) is Phase 49's job, one app per session.
