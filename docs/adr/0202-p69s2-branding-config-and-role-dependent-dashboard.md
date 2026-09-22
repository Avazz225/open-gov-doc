# 0202 — P69-S2: Branding Config + Role-Dependent Dashboard — Build

**Status:** accepted
**Context:** P69-S2 (Phase 69, second session), building per P69-S1's scoping decision
([ADR 0201](0201-p69s1-branding-and-role-dependent-dashboard-scoping.md)): an installation-level
branding config (product name/accent color/logo) layered on top of the static `libs/dms-ui` token
system, exported/imported as an 11th config-service singleton category, plus a role-dependent
dashboard. This ADR records what was actually built, including two real deviations the scoping
session's own design did not anticipate.

## What was built

**Backend**: `registry-service` gained a `BrandingConfig` singleton row (`id=1`, lazy-seeded on first
access, same pattern as `workflow_service.models.FederationConfig`) and `GET`/`PUT
/installation/branding` — `GET` ungated (must render on the login screen, before any token exists),
`PUT` gated behind `admin.object_config` (reused, not a new capability; this service's first ever
`PermissionServiceClient` consumer via the shared `dms-permission-client`, itself only just built in
P68-S2). `config-service` gained a matching `RegistryServiceClient` and `branding_config` as an 11th
singleton category (`SINGLETON_CATEGORIES`, mirroring `sensor_config`/`federation_config`'s shape
exactly — a single dict, not a name-keyed list).

**Frontend**: `user-ui`/`admin-ui`/`reviewer-ui`/`process-designer`/`migration-console` each gained a
`BrandingProvider` (deliberately duplicated per app, ADR 0006 — identical implementation, not a shared
package) that fetches the branding config once at load (admin-ui additionally re-fetches on
`switchInstallation`, the one app with a multi-installation concept) and applies `product_name` to
`document.title` and each app's login heading (`admin-ui` additionally to its home page title), and
`accent_color` as a `--dms-accent`/`--dms-accent-bg`/`--dms-accent-bg-strong` inline override —
converted from the admin-supplied hex to the same 0.15/0.5 alpha `rgba()` shape `tokens.css` itself
uses for light/dark, and deliberately skipped in high-contrast mode (that theme's accent values are a
legibility-driven fixed choice per `tokens.css`'s own comment, not a brand color to override).
`office-addin` was explicitly excluded (documented in `docs/services/office-addin.md`, same "follow the
host" reasoning as its existing theme/locale decisions, ADR 0168/0167) — no login screen or landing page
exists there for a brand identity to meaningfully attach to.

`admin-ui` additionally gained a role-dependent dashboard: `DashboardWidgets` renders one card per
`AdminSidebar` nav group with at least one capability-visible item, reusing `AdminSidebar`'s own
exported `GROUPS`/`visibleItems` (new shared helper) rather than a second authorization mechanism.
`user-ui` was deliberately NOT given dashboard widgets — its "home" is already the working document
workspace, not an empty landing page (see `docs/services/user-ui.md`).

## Deviation 1: the scoping session's "OG Doc" precedent was wrong, corrected before building

Already caught and corrected in ADR 0201 itself before this session began — noted here only for
completeness: the real hook used is `registry-service`'s pre-existing `GET /installation`/
`DMS_INSTALLATION_DISPLAY_NAME`, not the P46 rebrand's hardcoded i18n strings.

## Deviation 2: the gateway needed a genuinely new mechanism, not anticipated by the scoping session

`GET`/`PUT /installation/branding` share one path with opposite authorization needs — a first in this
project. Every existing `gateway-service` `public_routes` entry was previously either read-only on its
own path, or used pre-auth on a path with no authenticated counterpart, so `route_key =
f"{service_type}:{path}"` never needed to know the HTTP method. Making the whole path public (the naive
first approach) would have silently broken the `PUT` admin flow: for a public route `proxy()` never
sets `X-DMS-Principal` at all, regardless of whether a valid bearer token is present — the exact same
failure mode already once found and fixed for `config-service:config/import` (ADR 0058) recurring here.

**Fix**: `proxy()` now also computes `method_route_key = f"{request.method} {route_key}"` and checks it
alongside the existing `route_key` against both `public_routes` and `maintenance_mode_allowed_routes`.
A bare entry keeps matching every method on that path (every existing entry, unchanged). A
`"<METHOD> "`-prefixed entry matches that one method only — `"GET
registry-service:installation/branding"` is the first and, so far, only such entry. See
`docs/services/gateway-service.md` "Method-Scoped Public Routes" for the full rationale and
`test_branding_get_bypasses_gateway_auth_check`/`test_branding_put_still_requires_gateway_auth_check`
for the regression coverage.

## Verification

Each of the six touched services (`registry-service`, `config-service`, `gateway-service`) and five
frontend apps has its own test suite green (registry-service 58/58 incl. 4 new; config-service 53/53
incl. 1 new import round-trip + extended export/compare assertions; gateway-service 28/28 incl. 2 new,
the 3 pre-existing unrelated failures documented since P68-S1 unchanged; admin-ui 296/296 incl. 6 new
across `dashboard-widgets`/`branding-context`; user-ui/reviewer-ui/process-designer 287/287, 50/50,
50/50 respectively incl. 3-4 new branding tests each; migration-console 19/20, the 1 pre-existing
unrelated `transfer-console.test.tsx` failure confirmed via a stash-based baseline comparison). Every
touched service/app rebuilt, redeployed, and live-verified against the real running stack: `PUT
.../branding` round-tripped through the real gateway with a real `config-admin` token, `GET` confirmed
reachable pre-login via `curl`; all five frontend apps' login screens screenshotted showing the live
product-name override; `admin-ui`'s dashboard screenshotted in both light and dark themes showing
genuine capability-based widget gating (`users-admin`'s real permission set correctly hides
storage/diagnostics/license widgets it has no capability for).

## Consequences

- `registry-service` now has its own `PermissionServiceClient`/RBAC surface where it previously had
  none — a small, deliberate architectural addition, not a broadening of an existing pattern.
- The gateway's method-scoped public-route mechanism is now available for any future endpoint that
  needs the same "public read, gated write on the same path" shape, without inventing a new mechanism
  again.
- `office-addin`'s exclusion is a decision, not an oversight — revisit only if this app's UX changes to
  include an actual login screen or landing page of its own.
