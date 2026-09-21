# 0157 — Fine-grained user tracking for privileged accounts

**Status:** accepted (P41-S3, see Phase 41 in `IMPLEMENTATION_PLAN.md`)
**Context:** Phase 41 Session 3 (Compliance gaps from the concept document, last session of the phase),
affects `auth-service`, `gateway-service`, `permission-service`

## Decision

Concept 5.5, verbatim: *"vollständige Session-Metadaten (u. a. Client-IP, Geräte-/Browser-Fingerprint,
verwendetes Endgerät soweit ermittelbar, Authentifizierungsmethode, Zeitpunkt/Dauer der Session, ggf.
bekannte Netzwerk-/Standortinformationen)"* for privileged accounts — *"Standard: aktiv für den
aktivierten Superuser (4.6), individuell zuschaltbar für andere ... privilegierte Konten"* — with a
*"gesonderte, konfigurierbare Aufbewahrungsdauer (Standardwert: 7 Tage)"*, separate from the regular
audit log (5.3), and access restricted to *"nur wenige, explizit berechtigte Rollen"*. This is a
genuinely new feature area — no session/login-event table, no device/IP capture, and no notion of
"privileged account" existed anywhere in this project before this session.

The concept's field list is explicitly non-exhaustive ("u. a.") and names neither a toggle authority,
a concrete meaning for "network/location info", nor a "privileged account" definition. Re-reading the
text closely resolved two of these without a user decision: the toggle is framed entirely in the third
person ("individually switchable for other privileged accounts") with no self-service framing, so it is
admin-driven by construction; and the superuser default is explicitly tied to *activation* ("active
**while** activated"), not to a standing per-account flag — so no automatic "privileged account"
enumeration is needed at all: any principal can be individually toggled by an admin, with the superuser's
own activation state as the one hardcoded default-on case.

Two genuine scope decisions remained and were resolved via research/precedent, consistent with this
project's pattern of only escalating a user question when precedent doesn't already settle it:

- **No GeoIP/network-location lookup, only the raw client IP.** The concept hedges this field as
  optional ("ggf. bekannte...") and names no mechanism. A real lookup would be a genuinely new class of
  external dependency (a GeoIP database or API) with no precedent anywhere in this project, for a field
  the concept itself doesn't require.
- **No client-side device/browser fingerprinting, only the server-side `User-Agent` header.** "Device/
  browser fingerprint... as far as determinable" (the concept's own hedge) is honestly satisfied by what
  the server can already see without new instrumentation — real fingerprinting (canvas/font enumeration)
  is a frontend-JavaScript technique that would need new client-side code with no precedent anywhere in
  this project's six frontends.

**Two separate RBAC capabilities**, not one — `admin.user_tracking` (toggle a principal's tracking,
configure retention) and `admin.user_tracking_view` (view already-collected session data) — extending the
asymmetric-risk split this project already uses for `admin.attribute_pseudonymization`/`admin.attribute_
reveal` (Post-Roadmap Phase 41 Session 2, ADR 0156): toggling reduces future exposure, viewing exposes
already-captured behavioral data about a specific person.

## Rationale

- **Why hook into the access-token-minting endpoints (`/login`/`/refresh`/`/oidc/callback`) rather than
  a new middleware or a `Session` concept**: these are the only three places a token is ever actually
  issued, regardless of auth path (technical-account/Keycloak/SSO) — one shared helper,
  `_maybe_track_session_event`, decodes the freshly-minted access token (not any caller-supplied claims)
  so the exact same code path works for all three, and never needs to know which branch produced the
  tokens.
- **Why decode the freshly-minted token instead of using request-time claims**: uniform across both
  local (technical-account) and Keycloak-issued tokens — `local_token_issuer.mint_token`'s own docstring
  already establishes that both carry the identical claim shape (`sub`, `preferred_username`) by design,
  so one decode call via `app.state.combined_validator` (which already validates both issuers) covers
  every case without per-branch special-casing.
- **Why the superuser default is tied to live activation state, not a persisted flag**: mirrors the
  concept's own wording as closely as possible ("active *while* activated") and avoids a real
  synchronization hazard — a persisted flag would need to be set/cleared on every activate/deactivate
  cycle (including the automatic poll-loop expiry), and a missed update in either direction would
  silently produce the wrong tracking state for exactly the account this feature cares about most.
- **Why the gateway needs a new `X-DMS-Client-IP` header rather than relying on `request.client.host`
  inside `auth-service`**: `auth-service` sits behind the gateway — without this header, every captured
  IP would be the gateway container's own address, not the real client's. `gateway-service` already
  computed `request.client.host` for its own rate limiting; this session forwards the same value
  downstream for the first time. It had to be set OUTSIDE the existing `if route_key not in settings.
  public_routes` branch (via `.update()`, not reassignment) because `/login`/`/refresh`/`/oidc/callback`
  are themselves public routes (no bearer token exists yet at that point) — the previous code only ever
  populated `identity_headers` inside that branch.
- **Why a tracking failure must never break login**: `_maybe_track_session_event` wraps its entire body
  in a blanket `except Exception` with only a logged warning. A compliance feature that could
  accidentally lock every user out of the system on a tracking bug would be a strictly worse outcome
  than occasionally missing a tracked event.

## Consequences

- **New tables** (`auth` schema): `user_tracking_config` (per-principal opt-in, `principal_id` PK),
  `user_tracking_session` (one row per tracked login/refresh), `user_tracking_retention_config`
  (singleton, default 7 days). No `ALTER TABLE` needed — all three are genuinely new tables, `create_
  all` suffices.
- **New endpoints**: `GET`/`PUT /user-tracking-config/{principal_id}`, `GET /user-tracking-sessions?
  principal_id=`, `GET`/`PUT /user-tracking-retention-config` — gated as described above.
- **New RBAC**: `admin.user_tracking`/`admin.user_tracking_view` (roles `domain-admin-user-tracking`/
  `domain-admin-user-tracking-view`), auto-seeded on every fresh installation like every other
  domain-admin role in this project.
- **`gateway-service` forwards client IP to every downstream service unconditionally** — a new,
  small but installation-wide behavior change (previously computed only internally). No dedicated
  gateway-level test for the forwarding itself (would need a synthetic header-echoing test target with
  no existing precedent in that service's test suite) — verified instead via live E2E through the real
  running gateway with a real login.
- **The concept's "deletion obligation"-adjacent framing (via 5.3's contrast) is only partially
  addressed**: retention IS enforced (own poll loop, configurable, default 7 days, independent of the
  regular audit log) — but no GeoIP/location data, no client-side fingerprinting, no correlated session
  duration, and no four-eyes on the toggle action are built (the concept names the last as explicitly
  optional; still genuinely open as of Phase 65+'s gap-analysis round — `put_user_tracking_config` has no
  `requires_approval` wiring, unlike the AD-group-mapping endpoints, which do — small fix if ever pursued,
  same pattern as ADR 0171). ~~No admin-UI page either — API/curl-only~~ — **closed in Post-Roadmap Phase
  52 Session 1**: `apps/admin-ui/src/app/user-tracking/page.tsx` + `components/UserTracking.tsx`, gated
  by `RequireCapability`. All explicitly documented as deferred, not silently missing, in
  `docs/services/auth-service.md` "Open Points".
- **This closes Phase 41** ("Compliance Gaps from the Concept Document") — `graphify update .` runs at
  session end per the standing phase-end rule.
- **Tests**: `services/auth-service/tests/test_user_tracking.py` (new, 11 tests) covers RBAC (403s for
  both capabilities, including the toggle-vs-view split), the enable/disable roundtrip, a login/refresh
  NOT recorded while disabled vs. recorded once enabled (including real `X-DMS-Client-IP`/`User-Agent`
  capture), retention config defaults/validation, and the activated superuser being tracked with
  zero `UserTrackingConfig` rows at all (calling `superuser.activate()` directly to isolate the
  activation-tied default from the full four-eyes approval flow). `services/gateway-service` suite
  unchanged (29 tests) — the header-forwarding fix is additive and doesn't change any existing
  assertion. Full suite: `auth-service` 131 tests (previously 120).
