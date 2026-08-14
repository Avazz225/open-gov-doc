# 0009 — Cross-UI theming: Keycloak user attribute instead of a new persistence component

**Status:** accepted
**Context:** Concept 8, Session P4-S6 (user feedback after the first real browser test of the MVP)

## Decision

Light/dark/high-contrast/automatic is stored as a preference **on the user
account**, not only locally in the browser, so that it applies across
devices. The storage location is a declared Keycloak user attribute
(`dms_theme`) instead of a new persistence component:

- `auth_service.bootstrap.ensure_realm_and_client` idempotently declares
  `dms_theme` in the realm-wide Declarative User Profile
  (`_ensure_theme_attribute`), with
  `permissions: {view: [admin, user], edit: [admin, user]}`.
- `auth_service.admin_users.get_theme_preference`/`set_theme_preference`
  read/write the attribute via the existing admin client
  (`build_admin_client`, present since P4-S3 for user management).
- New endpoints `GET/PUT /me/preferences` on the Auth Service
  (`ThemePreference` schema, `Literal["light", "dark", "high-contrast",
  "auto"]`), self-service via the bearer token (`user["sub"]` = Keycloak
  user ID), no admin call needed by the client itself.
- Both frontends (User UI, Admin UI) each get their own, deliberately
  duplicated `theme-context.tsx` (ADR 0006: no shared domain logic between
  independently deployable apps) with `ThemeProvider`/`useTheme()`:
  `localStorage` (`dms.theme`) as an immediately available cache (no waiting
  for the first server response, also works on the login page without a
  session), synchronized with the server attribute as soon as an
  `accessToken` is available.
- `data-theme` is set on `document.documentElement` (`useLayoutEffect`, to
  avoid a visible flash of the wrong theme before the first paint) and
  drives the entire existing stylesheet of both apps via CSS variables
  (`--dms-bg`, `--dms-fg`, `--dms-border`, `--dms-accent`, ...).

## Rationale

- **Why a Keycloak attribute instead of a new service/table**: according to
  the concept, user accounts already live entirely in Keycloak (no own user
  schema in `auth-service`, see `docs/services/auth-service.md`). A pure UI
  preference of an existing account does not justify a new persistence
  component - that was already the premise from `IMPLEMENTATION_PLAN.md` for
  this session.
- **Pitfall: Declarative User Profile**: Keycloak 25+ (default since that
  version, used here) **silently** drops any attribute not declared in the
  realm profile on `update_user` - no error, just no effect. An initial test
  run without `_ensure_theme_attribute` showed exactly that: `PUT
  /me/preferences` returned 200, but a subsequent `GET` still returned
  `"auto"`. The cause was found via a direct comparison of
  `admin.get_realm_users_profile()` (only `username`/`email`/`firstName`/
  `lastName` declared). Without this declaration, the entire feature would
  have been silently ineffective.
- **`localStorage` cache despite server persistence**: without it, every app
  would have to wait until the first `/me/preferences` response before a
  theme is settled - including a visible jump on the login page (no token
  exists there yet). The cache makes the theme immediately available; a
  later sync overwrites it once the server response arrives. Deliberate
  simplification: no conflict-resolution mechanism if server and local value
  diverge (e.g. a second device changed it in the meantime) - last fetch
  wins, no retry on a failed `PUT`.
- **`useLayoutEffect` instead of assignment during render** (unlike the
  `gatewayBaseUrl` singleton in ADR 0008): here there is no child component
  that, in the *same* render pass, synchronously depends on the new value -
  `useLayoutEffect` is sufficient to set the attribute before the browser
  paint, and stays closer to React convention.
- **Why four levels (light/dark/high contrast/automatic) instead of just
  `prefers-color-scheme`**: "high contrast" is not a native browser color
  scheme and cannot be forced via `color-scheme` - it needs its own, opaque
  (non-transparent) accent colors, see `globals.css` in both apps.
  "Automatic" nonetheless remains the default, so as not to force a choice
  on users who have not made one explicitly.

## Consequences

- A user account has **one** theme preference, regardless of which app
  (User UI/Admin UI) or which device it logs in with - however, Admin UI
  multi-installation setups (ADR 0008) have a separate account per
  installation, and therefore potentially a separate theme preference per
  installation (no shared value across installation boundaries, consistent
  with the isolation there).
- The `dms.theme` `localStorage` key in the Admin UI is deliberately **not**
  installation-specific (unlike `dms.tokens.<id>` in ADR 0008) - switching
  to a different installation briefly still shows the last cached theme
  choice, until the new installation has loaded its own preference. Accepted
  simplification for this base scaffold, not a correctness issue (only a
  brief visual intermediate state).
- No retry/conflict resolution on a failed `PUT /me/preferences` - the
  selection continues to apply locally immediately; a failure of server
  persistence goes unnoticed (no UI error indication). Accepted for a base
  scaffold without a multi-device scenario in the test focus.
- The theming behavior itself (switching in the popover/header, no flash on
  reload) is only verified via Vitest component tests - no browser is
  available in this development environment (see `docs/services/user-ui.md`).
