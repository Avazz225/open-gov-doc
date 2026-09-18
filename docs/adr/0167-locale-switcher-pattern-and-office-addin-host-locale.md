# 0167 — Locale-switcher pattern (`process-designer`), `office-addin`'s host-locale decision, and a
Keycloak profile-wipe bug fix

**Status:** accepted
**Context:** P47-S1 (Phase 47, "English i18n"). ADR 0007 already anticipated a second language as "just
an additional JSON file" — this session builds the one genuinely missing piece no app has today: a
runtime language switcher with cross-device persistence. `process-designer` was chosen as the build
target over `office-addin` (both ~50-67 keys) because it already has the exact persistence mechanism to
mirror end-to-end (`ThemeProvider`/`ThemeSwitcher.tsx`, ADR 0009's `/me/preferences`), with zero
host/platform ambiguity — `office-addin` runs embedded inside Word, which raises its own question about
whether it should even have an in-app switcher at all (see below). Building the pattern once, cleanly,
in the simpler app avoids conflating the switcher's own design with that separate question.

## Decision

**Backend**: extended the existing `ThemePreference`/`GET PUT /me/preferences` mechanism
(`auth-service`) with a new `locale: "de" | "en"` field, persisted the same way as `theme` — as its own
Keycloak user attribute (`dms_locale`, declared in the realm's Declarative User Profile, same as
`dms_theme`). Critically, `PUT /me/preferences` now takes a **new** `PreferencesUpdate` request model
with both fields `Optional[None]` (not defaulting to `ThemePreference`'s real defaults) — only a field
actually present in the request body is written. Every existing caller before this session sends only
`{"theme": ...}`; without this, that call would have silently reset `locale` back to `"de"` on every
theme-only update, and vice versa once `process-designer` starts sending locale-only updates.

**Frontend** (`process-designer`): a new `LocaleProvider`/`useLocale()` (`lib/locale-context.tsx`)
mirrors `ThemeProvider` — cached `localStorage` value applied on mount, synced from the server once
`accessToken` is available, write-through `setLocale` that updates state immediately and PUTs to the
server fire-and-forget (no retry on failure, same as theme). It renders `I18nProvider` internally
(moved out of `layout.tsx`, which previously passed it a static `locale` prop), making `locale`
stateful for the first time. A new `LocaleSwitcher.tsx` mirrors `ThemeSwitcher.tsx`; both switchers are
now wired into `RequireAuth.tsx` (the one shared chrome across all three authenticated pages) — as a
side effect, `ThemeSwitcher` itself goes from completely dead/unwired code to actually working.

**`office-addin` gets no code this session** — this session only records the decision: it will keep
following the host Word application's own locale rather than gain an in-app switcher, the same
reasoning already applied to its existing "no theme switcher" precedent (`docs/services/
office-addin.md:76`: space constraints *and* "should follow the host"). Locale is a cleaner fit for
"follow the host" than theme ever was — a document editor embedded in Word displaying UI text in a
different language than Word itself would be actively confusing, not just a missed convenience. No
host-locale-detection code is built yet either; that's P47-S2's job once it needs to actually act on
this decision.

**A real, pre-existing bug was found and fixed in the same session**: `admin_users.py`'s
`set_theme_preference` (present since P4-S6) — and the new `set_locale_preference`, which initially
copied the same code — called `admin.update_user(user_id, {"attributes": attributes})`. Keycloak's
`PUT /admin/realms/{realm}/users/{id}` treats the request body as the **full** user representation, not
a merge; sending only `attributes` silently wiped `firstName`/`lastName`/`email` on every single
preference change. Found live during this session's own end-to-end verification (a throwaway Keycloak
test user's profile lost those fields after two preference updates, and could no longer log in
afterward — Keycloak's Declarative User Profile rejects a login with those fields missing). Fixed by
spreading the just-read full user representation before overriding `attributes`:
`admin.update_user(user_id, {**raw, "attributes": attributes})`, in both setter functions. Covered by a
new regression test (`test_update_preferences_does_not_wipe_other_profile_fields`).

## Rationale

- **Why `process-designer` over `office-addin` as the build target**: `office-addin`'s own open
  question (in-app switcher vs. follow-the-host) needed resolving anyway before it could get one — done
  as a pure decision, not entangled with proving out the switcher mechanics for the first time.
  `process-designer` had no such ambiguity and the closest existing persistence precedent to copy.
- **Why a new `PreferencesUpdate` model instead of extending `ThemePreference` itself as the request
  body**: `ThemePreference`'s fields default to `"auto"`/`"de"` — reusing it as the request body would
  make every field-omitting client (all of them, before this session) implicitly send the *default*
  for the other field on every update, not "leave it alone". Optional-with-`None`-default on a
  dedicated model is the only shape that expresses "unset means don't touch this field".
- **Why `locale` as its own Keycloak attribute instead of folding it into `dms_theme`**: keeps both
  preferences independently readable/writable without any coupling — the same reasoning `theme` and
  `locale` already get separate columns in the `ThemePreference` response.
- **Why the fix spreads `raw` instead of, e.g., a dedicated merge-attributes-only Keycloak Admin API
  call**: `python-keycloak`'s `update_user` has no partial-update variant; re-reading the full
  representation immediately before writing it back (already being done via `admin.get_user` for the
  attribute read itself) is the smallest correct fix, with no new dependency or API surface.
- **Why this bug wasn't caught earlier**: real end-user Keycloak logins with profile data
  (`firstName`/`lastName`/`email`) exercising `/me/preferences` are rare in daily dev-stack testing,
  which mostly uses technical accounts (`users-admin`, `config-admin`, ...) or direct
  `X-DMS-Principal` header calls that bypass this code path entirely — and technical accounts aren't
  real Keycloak users at all (`admin.get_user()` 404s against them), so they never triggered it either.
  Only became visible once this session live-tested against a real Keycloak user with a full profile.
- **Why the static-export hydration fix was needed**: `ThemeProvider` safely seeds its initial React
  state from `localStorage` because `theme` only drives an imperative `document.documentElement.dataset
  .theme` attribute set in a `useLayoutEffect` — never anything React itself renders. `locale` is
  different: it selects which JSON dictionary `t()` reads from, i.e. it drives the actual rendered text
  tree. Since `process-designer` is a static export, the server always pre-renders `defaultLocale`'s
  ("de") strings; seeding `LocaleProvider`'s initial state directly from a cached non-default locale
  mismatched that markup on hydration (React error #418, confirmed live via Playwright on a page
  reload after switching to English). Fixed by starting `useState` at `defaultLocale` (matching the
  server-rendered markup exactly) and applying the cached value in a `useEffect` that only runs after
  mount — the same "server and first client render must match" constraint any client-only preference
  needs to respect once it's allowed to change *rendered text*, not just an attribute.

## Consequences

- **`auth-service`**: `GET /me/preferences` now returns `{"theme", "locale"}` instead of just
  `{"theme"}` — additive, no existing caller reads a fixed key set. `PUT /me/preferences` now accepts
  `PreferencesUpdate` instead of `ThemePreference` as its body — every existing `{"theme": ...}`-only
  caller continues to work unchanged (locale stays whatever it already was). The Keycloak profile-wipe
  fix is a pure bugfix with no API shape change.
- **`process-designer`**: gains `LocaleProvider`/`useLocale()`, `LocaleSwitcher.tsx`, an `en.json`
  dictionary, and now has both a working locale switcher and (as a side effect) a now-actually-wired
  theme switcher, visible in `RequireAuth.tsx`'s top bar across all three authenticated pages.
- **`office-addin`**: no code change, but its host-locale-follow behavior is now a recorded decision
  rather than an open question — P47-S2 (or whichever session eventually touches its i18n) should build
  toward "detect Word's locale and select the matching dictionary automatically", not an in-app
  switcher.
- **No admin-UI/config surface for the Declarative-User-Profile attribute declarations** — same
  backend-first precedent as `dms_theme` before it (`bootstrap.py` remains the only place either
  attribute is declared).
- **The Keycloak wipe bug's blast radius before this fix**: any real (non-technical-account) Keycloak
  user who ever called `PUT /me/preferences` in any app since P4-S6 would have lost `firstName`/
  `lastName`/`email` from their Keycloak profile. No evidence this happened in the dev stack outside
  this session's own throwaway test user (repaired and then deleted as part of this session's live
  verification) — real end-user preference usage against non-technical accounts appears to be rare so
  far, but any installation that has been running since P4-S6 should audit real users' Keycloak
  profiles for this if theme/locale preferences have seen real use.
