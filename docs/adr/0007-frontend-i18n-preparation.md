# 0007 — Frontend i18n: JSON dictionary + React context, only German active

**Status:** accepted
**Context:** Concept 8 ("adaptability"), Session P4-S3 (retroactively added for both existing frontend apps)

## Decision

Both frontend apps (`apps/user-ui`, `apps/admin-ui`) get an identical,
minimal i18n base scaffold: all visible text lives in a language file
(`src/i18n/de.json`, nested keys like `login.heading`), an
`I18nProvider`/`useI18n()` context (`src/i18n/index.tsx`) resolves
`t("area.key")` calls at runtime against the currently active language.
Components no longer contain hardcoded German strings, with the exception of
Next.js's `metadata` export (see Rationale). **Only German is active**
(`defaultLocale = "de"`, no language switcher in the UI) — the base scaffold
is deliberately built so that a second language can be added later without
component changes.

## Rationale

- **Why now, not only when needed**: brought forward at explicit request —
  extracting strings retroactively from components that have already grown
  gets more expensive with every further UI session (Reviewer UI, migration
  console, Concept 8/14). A base scaffold without i18n preparation would have
  built up technical debt here.
- **JSON dictionary + own context instead of a library** (e.g. `next-intl`,
  `react-i18next`): the common Next.js i18n solutions are built around
  routing-based language switching (`/de/...`, `/en/...`) or middleware —
  both incompatible with the static export without a Node runtime server
  (ADR 0006). A pure client-context solution with a static dictionary import
  needs no server component and no dynamic routes, so it fits exactly with
  the existing CSR model.
- **One language file per app instead of a shared i18n lib**: consistent with
  the decision already made that frontend apps do not import shared domain
  logic (ADR 0006, `auth-context.tsx` is likewise duplicated per app) — the
  dictionaries differ completely between the User UI and the Admin UI anyway
  (different areas, different terminology).
- **The `metadata` export remains a direct JSON import instead of
  `useI18n()`**: Next.js evaluates `export const metadata` server-side/at
  build time, before any client component (and thus the context) exists — a
  re-export from the i18n module marked `"use client"` broke the
  `_not-found` page build (`Cannot read properties of undefined`). Layouts
  therefore import `de.json` directly, while all interactive components
  continue to go through `useI18n()`.

## Consequences

- Adding a second language means: create a new JSON file (e.g. `en.json`,
  same key structure), register it in `dictionaries`, extend the `Locale`
  type — no component needs to be touched, since all of them speak
  exclusively via `t()` paths.
- There is still **no language switcher in the UI** (no locale switcher, no
  persistence of a user preference) — `I18nProvider` currently only receives
  `locale` as an optional prop with default `"de"`. This follows once a
  second language actually exists; until then a switcher without an
  alternative would be pointless.
- If a key is missing from the dictionary, `t()` returns the path itself
  (e.g. `"login.heading"` instead of text) rather than crashing —
  deliberately defensive for a single, still-small dictionary; as scope
  grows, a build-time check for missing keys (e.g. via a script or a test
  case per language file) would make sense, but is not yet part of this
  session.
- Translations for future backend-generated text (e.g. error messages from
  `HTTPException(detail=...)`) are unaffected by this — the backends
  continue to deliver plain German text, which is shown 1:1 in `error-text`
  fields. Full internationalization would also need to deliver this text in
  structured form (e.g. error codes instead of plain text) — deliberately
  not part of this session, since the backends have no internal i18n concept
  and the concept does not require this for the API layer either.
