# 0008 — Admin UI multi-installation: client-side installation list, session per installation

**Status:** accepted
**Context:** Concept 3a/8, Session P4-S5 (user feedback after the first real browser test of the MVP)

## Decision

The Admin UI manages a list of configured installations (`{id, name,
gatewayBaseUrl}`), purely client-side in `localStorage`
(`dms.installations`, `dms.activeInstallationId`) — no new backend service
for this, since this list is a pure UI preference, not domain data (see
Concept 3a: an installation only knows itself, there is no central instance
allowed to know "all installations", except for the optional Fleet
Management Service, not built here).

Technically:

- `lib/api.ts`'s previously fixed-imported `GATEWAY_BASE_URL` constant is
  replaced by a **mutable module variable** (`gatewayBaseUrl`) plus a setter
  (`setGatewayBaseUrl()`). All existing callers (`UserManagement`,
  `ObjectTypeEditor`, `RegistryOverview`, `auth-context.tsx`) remain
  unchanged — they never know a URL, only `service_type`/path.
- `InstallationProvider` (`lib/installation-context.tsx`) holds the list +
  active installation and calls `setGatewayBaseUrl()` synchronously during
  render (not in a `useEffect`), since `AuthProvider` as a child component
  already needs the current address in its own first render effect — effects
  fire bottom-up, so a `useEffect` in `InstallationProvider` would come too
  late for that.
- `AuthProvider` gets its own **`localStorage` key per installation**
  (`dms.tokens.<installationId>` instead of the previous global
  `dms.tokens`) and loads/saves sessions exclusively for the currently
  active installation, without touching other installations' sessions.
- Provider order in `layout.tsx`: `I18nProvider > InstallationProvider >
  AuthProvider` — `AuthProvider` needs the active installation to form its
  storage key.

## Rationale

- **Why not simply use multiple browser tabs/profiles**: that was exactly
  the alternative named by the user that was meant to be avoided — "you
  shouldn't have to log in n times". An installation list with a switcher
  within a single running Admin UI instance is the solution explicitly
  required by the concept (8).
- **No single sign-on across installation boundaries**: this would
  contradict the deliberate isolation from Concept 3a (each installation has
  its own, fully independent identity management/Keycloak realm). Every
  installation therefore still needs its own, one-time login — the only
  convenience gained is that later switching *back* to an already logged-in
  installation does not require logging in again, as long as its session is
  still valid.
- **Mutable singleton instead of context/prop-drilling through `api.ts`**:
  `api.ts` is a pure function module, not a React tree — it cannot consume a
  context. Extending every caller function with a `gatewayBaseUrl` parameter
  would have touched every existing component and every existing test case,
  for a case (installation switching) that happens rarely per session. A
  single setter, called on every switch, is the smaller, less invasive
  change.
- **Side effect during render instead of in `useEffect`**: a deliberate,
  documented deviation from the React convention "no side effects during
  render" — here it is a plain assignment to an external module variable (no
  DOM/no subscription), idempotent when run repeatedly with the same value,
  so uncritical even under React Strict Mode. The alternative (`useEffect`
  in the provider) would have introduced a race condition:
  `AuthProvider`'s own session-restoration effect (child effect, fires
  first) would in certain cases have still run against the *old* gateway
  address.

## Consequences

- Installations are stored purely locally in the browser — no backup, no
  synchronization across an admin user's devices/browsers. This is a
  deliberate boundary of this base scaffold, not full provisioning (that
  would be the job of the optional Fleet/License Management Service, Concept
  3a, Phase 13).
- If an attempt is made to remove the last remaining installation,
  `InstallationProvider` actively prevents it (`removeInstallation` is a
  no-op when exactly one installation remains) — at least one installation
  must always remain configured, otherwise the entire rest of the Admin UI
  would have no valid gateway target.
- Multi-installation behavior (session isolation, switching without
  re-login) is only verified via Vitest component tests
  (`installation-context.test.tsx`, `auth-context.test.tsx`) — no browser is
  available in this development environment, see
  `docs/services/admin-ui.md`.
- The User UI has **no** equivalent multi-installation concept — per
  Concept 8 this is exclusively an Admin UI requirement (administrators may
  manage multiple installations, end users typically only work within their
  own).
