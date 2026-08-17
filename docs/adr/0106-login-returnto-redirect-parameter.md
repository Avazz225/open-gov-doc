# 0106 — `returnTo` redirect parameter in the login flow (all 6 apps), sessionStorage relay for SSO

**Status:** accepted (P27-S2/S3, see `IMPLEMENTATION_PLAN.md`)
**Context:** new post-roadmap feature (authenticated direct links, Phase 29), affects `apps/*/src/components/RequireAuth.tsx` (6 copies), `apps/*/src/app/login/page.tsx` (6 copies), `apps/user-ui/src/app/login/callback/page.tsx` ([ADR 0062](0062-sso-automatischer-login-oidc-redirect-und-optionales-kerberos.md))

## Decision

`RequireAuth.tsx`, on redirecting an unauthenticated visitor to the login
page, now appends the page they were trying to reach as a `returnTo` query
parameter: `router.replace(\`/login/?returnTo=${encodeURIComponent(target)}\`)`,
where `target` is `window.location.pathname + window.location.search`
read directly (no `next/navigation` `useSearchParams()`/`usePathname()`
hooks, avoiding the `<Suspense>` boundary that hook would otherwise require
under `output: "export"`, ADR 0006 — consistent with `login/page.tsx`'s own
pre-existing style of reading `window.location.search` directly for
`ssoError`). `login/page.tsx` reads `returnTo` back
(`new URLSearchParams(window.location.search).get("returnTo")`) and
redirects there after a successful login instead of hardcoding `/`, both
on manual form submit and for a visitor who is already logged in when they
land on `/login/`.

**Open-redirect protection**: a `sanitizeReturnTo()` helper only accepts
values starting with exactly one `/` (rejecting `//host/...` and `/\host/...`,
both of which browsers resolve as protocol-relative URLs to an arbitrary
host) and falls back to `/` for anything else, including absolute URLs
(`https://...`).

**SSO round trip (`user-ui` only, the only app with an SSO callback,
P27-S3)**: `returnTo` cannot be carried through Keycloak's own redirect —
`redirect_uri` is checked against a fixed, pre-registered origin allow-list
(ADR 0062) and would reject an arbitrary query string appended to it.
Instead, `login/page.tsx` stashes the sanitized value in
`sessionStorage` (`dms.sso.returnTo`) alongside the existing CSRF `state`
value (`dms.sso.state`) right before redirecting to Keycloak;
`login/callback/page.tsx` reads both back after the round trip completes
and redirects to the stashed target (re-sanitized on read, since
`sessionStorage` is plain client-side state a determined actor with script
execution could tamper with — the same threat model as any client-stored
value).

## Rationale

- **Duplicated across 6 apps instead of a shared lib**: `RequireAuth.tsx`
  and `login/page.tsx` were already 6 independent, near-identical copies
  before this change (only differing in their app-specific shell wrapper
  and a few comments) — consistent with this project's established
  precedent of not abstracting small, structurally identical logic
  duplicated across services/apps purely for DRY's sake (see Phase 20's
  poll-loop rationale in `IMPLEMENTATION_PLAN.md`/the Phase 18+ plan).
  Extracting a shared auth-UI package now would be a larger, separate
  refactor unrelated to this feature's actual goal.
- **Plain `window.location` reads instead of `useSearchParams()`**: avoids
  every `RequireAuth`/`login` consumer needing a new `<Suspense>` wrapper
  purely to satisfy Next.js' static-export prerendering requirement for
  that hook (see `process-designer/src/app/designer/page.tsx` for the
  existing, more invasive pattern this avoids) — `RequireAuth` in
  particular wraps nearly every page in every app, so adding a Suspense
  boundary there would have been a much larger blast radius for a
  read that's already done elsewhere in these same files via
  `window.location.search`.
- **`sessionStorage` relay instead of extending the Keycloak
  `redirect_uri` allow-list mechanism**: widening ADR 0062's allow-list
  to accept an arbitrary trailing query string would reopen exactly the
  open-redirect risk that allow-list exists to prevent; a value that
  never leaves the browser (stashed before, read back after, both on the
  same origin) carries no such risk and needs no server-side change.

## Consequences

- A direct link into `office-addin`/`process-designer`/`migration-console`
  now also correctly survives a login redirect (P27-S2 applies uniformly
  to all 6 apps), even though none of them are an actual target of Phase
  29's direct-links feature yet — free consistency, not scope creep, since
  the mechanism is identical either way.
- `returnTo` is only ever a same-origin relative path, never carries
  cross-app navigation (e.g. from `reviewer-ui`'s login back into
  `user-ui`) — each app's `RequireAuth` only ever redirects within its own
  origin, matching how these apps are already deployed as separate,
  independently hosted static sites (ADR 0006/0102).
- The SSO stash/read-back adds two small, easily-overlooked
  `sessionStorage` keys specific to `user-ui` — documented here and in the
  inline comments at both call sites so a future SSO-related change
  doesn't miss the second key while updating the first.
