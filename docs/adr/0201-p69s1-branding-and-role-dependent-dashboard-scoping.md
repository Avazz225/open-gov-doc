# 0201 — P69-S1: Branding/Theming Config + Role-Dependent Dashboards — Scoping

**Status:** accepted (scoping only — no code)
**Context:** P69-S1 (Phase 69, first session, ninth gap-analysis round). Two findings from the same
slice of `Konzept.md` that are really one gap: Concept 7.3's configuration-export category "UI
customizations" was never built (`docs/services/config-service.md`: *"Deliberately not included: 'UI
customizations' (branding/theming) — does not exist anywhere in the code (see ADR 0035)"*; ADR 0035
itself names it as an expected future precedent-follower), and Concept 8's *"customizability...
branding/theming, role-dependent dashboards where applicable"* is an unstruck, permanent Open Point in
`docs/services/user-ui.md` (line 466, verbatim: *"Role-dependent views/branding (Concept 8,
'customizability') not part of this foundation."*) that had never surfaced in any of the eight prior
gap-analysis rounds until now. This session is scoping only, per the plan's own Definition of Done — no
tests, no code, a decision record for P69-S2 to build against.

## What the plan's own hypothesis got right, and one thing it got wrong

The plan's own text speculated: an installation-level branding config (logo, accent color, product-name
override — citing the "OG Doc" rename as an existing precedent for product-name-as-config) layered on
top of the existing token system (`libs/dms-ui`, ADR 0168), exported/imported as a new 7.3 config
category; and "role-dependent dashboards" meaning conditional widget visibility by capability, not a
second UI.

Verified against the real code: the shape is right, but **the "OG Doc" precedent is wrong — it's the
opposite of what the plan claimed.** Phase 46 Session 1's rebrand was seven hardcoded i18n string edits
(`meta.title`/`login.heading`/`home.title` baked into each app's dictionary file) plus one PDF metadata
string — nothing about it is config-driven or per-installation; it's a single fixed brand shared by
every installation of the same build. It demonstrates the *absence* of product-name-as-config, not its
precedent.

The real, previously-unused hook already exists: `dms_common.BaseServiceSettings.installation_display_name`
(`DMS_INSTALLATION_ID`/`DMS_INSTALLATION_DISPLAY_NAME`, P13-S1/ADR 0032 addendum), exposed ungated via
`registry-service`'s `GET /installation` (`{id, display_name}`). No frontend consumes it today (zero
references across all six apps) — this is the natural seed for a real product-name override, not the
i18n rename.

## `libs/dms-ui`'s token system has no per-installation override mechanism today

`libs/dms-ui/tokens.css` is a plain static CSS file (not a package, not JS), `@import`-ed by each app's
`globals.css` (ADR 0168). It is 100% build-time/static: color/spacing/radius/shadow/typography tokens per
`light`/`dark`/`high-contrast` `data-theme` block, identical across every installation of the same build.
An accent-color override fits naturally as a single CSS custom-property override (`--dms-accent`/
`--dms-accent-bg`) applied at runtime — but logo and product name aren't tokens in this system at all (no
`--dms-logo-url`/text token exists) and would need a genuinely new mechanism: a small runtime-fetched
branding object each app applies on top of the static tokens (set `--dms-accent` inline via JS, swap a
logo `<img src>`/title string), not an extension of `tokens.css` itself.

## Config-service precedent: mirror `sensor_config`/`federation_config`, not a name-keyed category

Of config-service's ten existing export/import categories, two are the right shape to mirror:
`sensor_config` and `federation_config` — both documented singletons (a single PUT-able row), not
name-keyed lists like `roles`/`object_types`. A new `branding_config` category should follow the same
shape: one owner-service PUT endpoint, a `ConfigServiceClient.get_branding_config()`/
`put_branding_config()`, added to `CATEGORIES`. No existing singleton-settings table lives anywhere
central — `sensor_config` lives in `monitoring-service`, `federation_config` in `workflow-service`, each
service-local. `registry-service` is the natural owner for `branding_config` too, since it already owns
`GET /installation` and the two concerns (installation identity, installation branding) are adjacent.

## Role-dependent dashboards: the mechanism already exists, just not on a literal dashboard yet

Neither `user-ui`'s nor `admin-ui`'s home page has a widget/dashboard concept today — `user-ui`'s
`page.tsx` is a bare `RequireAuth`-wrapped `DocumentWorkspace`; `admin-ui`'s is a title plus one hint
paragraph. But the actual "show/hide by capability" mechanism the plan predicted already exists and is
proven: `AdminSidebar.tsx`'s `NavItem[]` array, each item carrying `requiresCapability?: string |
string[]`, filtered client-side against `useAuth()`'s capabilities, with `RequireCapability.tsx` as
defense-in-depth on the target page. Building "role-dependent dashboards" is extending this exact,
already-established pattern onto a new widget grid, not inventing a new authorization mechanism.

**Scope correction**: the Open Point as literally written in `user-ui.md` names only `user-ui` — but
`admin-ui` is structurally the better fit (domain-admin roles already gate its nav meaningfully;
`user-ui`'s roles are mostly document-permission-based, not dashboard-relevant, and its "home" is
already a working document workspace, not an empty landing page needing widgets). P69-S2 targets
`admin-ui`.

## Decision for P69-S2

1. **Branding config** — new registry-service-owned singleton `branding_config` (nullable `logo_url`,
   nullable `accent_color` hex, nullable `product_name` override), `GET`/`PUT /installation/branding`
   (mirroring `GET /installation`'s existing ungated-read/gated-write shape — write needs
   `admin.installation_branding` or equivalent, read stays ungated like `GET /installation` itself, since
   branding must render on the login screen before authentication). Added to config-service as an 11th
   singleton category, `sensor_config`/`federation_config` pattern. Each of the six frontend apps fetches
   once at load and applies `--dms-accent` inline + swaps title/logo, falling back to today's static
   values when unset — no change to `tokens.css` itself. `product_name` overrides the currently-hardcoded
   "OG Doc" i18n strings at render time, not by editing dictionary files.
2. **`office-addin`'s reduced theming (ADR 0168) gets an explicit decision, not silent inclusion**:
   P69-S2 must decide whether branding (especially `product_name`) applies there too, given its
   deliberate "follow the host Office application" stance for everything else visual.
3. **Role-dependent dashboard**: `admin-ui`'s home page becomes a widget grid, each widget gated by the
   same `requiresCapability` pattern `AdminSidebar.tsx` already established — near-zero new mechanism.
   `user-ui`'s Open Point is explicitly rescoped/struck as "not applicable to this app's actual home
   surface" rather than carried forward as unbuilt.

## Consequences

- **Underinstated complexity found**: six frontend apps means six places needing the branding-fetch-and-
  apply wiring (mechanical but real — the same "one app per session" cost Phase 49 already had for
  `tokens.css` rollout); `product_name`-as-config touches every app's i18n `meta.title` currently baked
  per-locale-file, more surface area than "logo + accent color" alone suggests.
- P69-S2's own DoD (already set in `IMPLEMENTATION_PLAN.md`) applies unchanged: a new ADR, tests, real
  browser verification (both themes) for the visual change, `docs/services/config-service.md`/
  `user-ui.md`/`admin-ui.md`/`registry-service.md` updated.
- No code, no tests, no doc corrections beyond this ADR in P69-S1 itself — `docs/services/user-ui.md`'s
  Open Point and `config-service.md`'s "deliberately not included" bullet are left as-is until P69-S2
  actually closes them, to avoid a doc claiming something not yet built.
