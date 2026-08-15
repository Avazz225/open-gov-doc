from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "gateway-service"

    registry_service_base_url: str = "http://localhost:8001"
    instance_cache_ttl_seconds: float = 5.0
    monitoring_service_base_url: str = "http://localhost:8026"

    keycloak_base_url: str = "http://localhost:8080"
    keycloak_realm: str = "dms"
    keycloak_client_id: str = "dms-api"

    # Auth decoupling from Keycloak (post-roadmap feature, Phase 18, ADR
    # 0063): since Phase 18, `auth-service` can issue tokens for local
    # technical accounts (superuser, domain admins in the future) in
    # addition to Keycloak itself - the gateway must additionally know
    # their JWKS to validate such tokens, see `_build_token_validator`.
    # Direct address (as with every other east-west call in this project),
    # no registry resolution needed, since `TokenValidator` only fetches
    # the JWKS lazily when the first local token actually needs validation.
    auth_service_base_url: str = "http://localhost:8003"

    # Browser frontends (user-ui/admin-ui, concept 8) run on a different
    # origin than the gateway - without CORS approval, the preflight
    # OPTIONS request already fails with 405, before the actual request is
    # even sent (curl-based tests don't cover this, since curl doesn't set
    # an Origin header and therefore doesn't trigger a preflight).
    cors_allowed_origins: list[str] = ["http://localhost:3000", "http://localhost:3001"]

    # "{service_type}:{path}" (path without leading slash, exact match) -
    # routes that must be reachable without a bearer token, first and
    # foremost login/refresh itself (you need a token to get a token, after
    # all). Since P6-S9, additionally the two federation-hub inbound
    # endpoints (7.4) - the hub is not a logged-in principal, it
    # authenticates instead via its own signature
    # (`X-Federation-Hub-Signature`, see
    # `workflow_service.main._verify_hub_signature`).
    # Since P13-S2, additionally four routes for the independently operated
    # `fleet-management-service` (3a) - it likewise has no logged-in
    # principal of this installation. The two read routes were already
    # ungated before gateway access (`registry-service`/`license-service`
    # never require a principal there); the two write routes instead check
    # the installation-wide `DMS_FLEET_AGENT_API_KEY` server-side (see
    # `_is_fleet_agent()` there) - the gateway itself does not know this
    # key, it only passes the `Authorization` header through untouched (no
    # more Keycloak token requirement on these four paths).
    # Since P14-S10, additionally the two public share-link endpoints
    # (4.2a) - anonymous viewers do not have a bearer token, the share
    # link's own token instead travels along as a query parameter
    # (`?token=`, see document-service.main._resolve_active_share_link),
    # so that these two entries can remain simple, static exact-match
    # strings, without changing wildcard/matching logic on the gateway
    # itself.
    # Since P15-S4, additionally the federated contact search (2.5/7.4) - a
    # peer installation has no bearer token of this installation, it
    # authenticates instead via `X-Installation-Signature` (see
    # auth_service.main.federated_search_inbound, same principle as
    # workflow-service's federation/inbound above).
    # Since P17-S1, `config-service:config/import` replaced (not just
    # supplemented) by `config-service:config/fleet-import`: the old,
    # shared path for both the fleet-agent key and real, logged-in admins
    # meant the gateway never validated a bearer token for ANY call to this
    # path - `X-DMS-Principal` therefore always stayed empty even for real
    # admins, the RBAC branch of `config-service`'s
    # `_require_import_permission` was effectively unreachable (found at
    # P17-S1, when the first admin-UI integration for configuration
    # packages, 14.1, was built). `POST /config/import` is now a regular
    # path requiring a Keycloak token; only the new, dedicated
    # `config/fleet-import` path remains public, see
    # `config_service.main.fleet_import_config`.
    public_routes: list[str] = [
        "auth-service:login",
        "auth-service:refresh",
        "auth-service:users/directory/federated-search-inbound",
        "workflow-service:federation/inbound",
        "workflow-service:federation/inbound-result",
        "registry-service:installation",
        "license-service:license/status",
        "license-service:license",
        "config-service:config/fleet-import",
        "document-service:public/share-links",
        "document-service:public/share-links/content",
        # SSO/automatic login (post-roadmap feature): the login entry point
        # itself and the code-exchange callback - at this point no DMS
        # token exists yet for the bearer check to verify.
        "auth-service:oidc/authorize",
        "auth-service:oidc/callback",
    ]

    # Emergency shutdown (4.8, P6-S6): while maintenance mode is active,
    # all proxied requests are rejected with 503, except these routes
    # (login/refresh/me/superuser status remain reachable, so at least the
    # superuser can log in - `auth-service` itself rejects any other
    # username, see `POST /login` there; the two maintenance-mode endpoints
    # themselves must naturally remain reachable during the lock).
    maintenance_mode_allowed_routes: list[str] = [
        "auth-service:login",
        "auth-service:refresh",
        "auth-service:me",
        "auth-service:superuser/status",
        # SSO/automatic login (post-roadmap feature): same reasoning as for
        # "auth-service:login" above - even during active maintenance mode,
        # at least the superuser must be able to log in, regardless of
        # which of the two login paths is used.
        "auth-service:oidc/authorize",
        "auth-service:oidc/callback",
        "permission-service:maintenance-mode",
        "permission-service:maintenance-mode/lift",
    ]
    maintenance_cache_ttl_seconds: float = 5.0

    # User feedback: 120/60s triggered 429 very quickly during completely
    # normal interactive use - a single page load of the three-column
    # workspace already fires a dozen parallel calls (folders, documents,
    # favorites, approval configuration, reference-number config, object
    # types, maintenance-mode poll every 30s, ...), real clicking easily
    # exceeded 120 within a minute as a result - not a bug in the client or
    # auth, just a default sized too tightly for this SPA (see
    # PROGRESS.md). Sized significantly more generously, but remains a
    # real safety net against clients that genuinely run out of control.
    rate_limit_max_requests: int = 600
    rate_limit_window_seconds: float = 60.0

    # Shared store for rate limiting (since P25-S3, ADR 0097) - replaces
    # the original in-process `dict` (ADR 0005), so multiple horizontally
    # scaled gateway instances see the same counter per client. Points by
    # default to the new `redis` service in `infra/docker-compose.yml`
    # (internal Compose DNS).
    redis_url: str = "redis://localhost:6379/0"

    upstream_timeout_seconds: float = 30.0
