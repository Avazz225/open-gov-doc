from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "auth-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    keycloak_base_url: str = "http://localhost:8080"
    # SSO/automatic login (post-roadmap feature): the Keycloak URL reachable
    # from the BROWSER for the redirect flow (`GET /oidc/authorize` returns
    # an `authorization_url` the browser navigates to itself). May differ
    # from `keycloak_base_url` - in the Docker Compose stack, `auth-service`
    # talks to Keycloak internally via `http://keycloak:8080`, but the
    # user's browser doesn't know this hostname and needs the externally
    # mapped `http://localhost:8080` instead. Falls back to
    # `keycloak_base_url` if not set (e.g. tests/local development without
    # Docker, where both are identical).
    keycloak_public_base_url: str | None = None
    keycloak_realm: str = "dms"
    keycloak_client_id: str = "dms-api"
    keycloak_client_secret: str = "dms-api-dev-secret"
    keycloak_admin_username: str = "admin"
    keycloak_admin_password: str = "admin_dev_only"

    permission_service_base_url: str = "http://localhost:8004"
    # First consumer of this service (P6-S5, superuser break-glass, 4.6).
    subjects: list[str] = ["permission.approval.approved"]

    # Break-glass activation (4.6): deliberate simplification of a single
    # absolute expiry timestamp instead of separate total-duration/
    # inactivity timers (see ADR 0023) - enforced via the same poll-loop
    # approach as workflow-service's SLA time monitoring (ADR 0020).
    superuser_activation_minutes: int = 30
    superuser_poll_interval_seconds: float = 30.0

    # Federated contact directory search (2.5/7.4, P15-S4) - own
    # registration independent of workflow-service's Federation Hub
    # participation (see models.py). Deliberately opt-in: stays
    # `None`/`False` until an installation explicitly configures this -
    # "no automatic side effect of the existing federation feature"
    # (concept 2.5, verbatim).
    federation_hub_base_url: str | None = None
    federated_directory_enabled: bool = False
    installation_gateway_base_url: str = "http://gateway-service:8000"

    # SSO/automatic login (post-roadmap feature, Kerberos/SPNEGO via
    # Keycloak): the browser redirect flow (`standardFlowEnabled` +
    # redirect URIs) is ALWAYS set up, independent of Kerberos - this is
    # the part that works even without Kerberos configuration (fallback to
    # Keycloak's own hosted login form). Only the Kerberos part itself is
    # tied to these three values; if one is missing, `_ensure_kerberos` in
    # bootstrap.py is cleanly skipped (no domain controller/keytab needed
    # in a fresh/sandbox-like installation to make SSO usable at all).
    kerberos_enabled: bool = False
    kerberos_realm: str | None = None
    kerberos_server_principal: str | None = None
    kerberos_keytab_path: str | None = None

    # Allowed origins for `redirect_uri` in `GET /oidc/authorize`
    # (open-redirect protection) - same list-setting pattern as
    # gateway-service's `cors_allowed_origins`. Deliberately only user-ui in
    # this session (see ADR) - further frontends follow the same pattern as
    # needed.
    sso_redirect_uri_allowed_origins: list[str] = ["http://localhost:3000"]

    # Auth decoupling from Keycloak (post-roadmap feature, Phase 18, ADR
    # 0063): validity duration of tokens for local technical accounts
    # (superuser, future domain admins) - independent of
    # `superuser_activation_minutes` above, which limits the break-glass
    # ACTIVATION (how long `enabled=True` stays), not the lifetime of a
    # single token. Values are oriented on Keycloak's own default token
    # lifetimes.
    local_access_token_ttl_seconds: int = 300
    local_refresh_token_ttl_seconds: int = 1800
