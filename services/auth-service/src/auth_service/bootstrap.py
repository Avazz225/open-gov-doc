from keycloak import KeycloakAdmin

from auth_service.settings import Settings


def _declare_profile_attribute(admin: KeycloakAdmin, *, name: str, display_name: str) -> None:
    """Shared declaration helper (P6-S5, extracted from the previous
    theme-specific function) - Keycloak's Declarative User Profile (the
    default since Keycloak 25) silently drops any undeclared attribute on
    `update_user`, no error, simply no effect."""
    profile = admin.get_realm_users_profile()
    if any(attribute["name"] == name for attribute in profile["attributes"]):
        return
    profile["attributes"].append(
        {
            "name": name,
            "displayName": display_name,
            "multivalued": False,
            "permissions": {"view": ["admin", "user"], "edit": ["admin", "user"]},
        }
    )
    admin.update_realm_users_profile(profile)


def _ensure_theme_attribute(admin: KeycloakAdmin) -> None:
    """Theme preference (8, P4-S6). Without this declaration,
    `admin_users.set_theme_preference` would silently have no effect."""
    _declare_profile_attribute(admin, name="dms_theme", display_name="DMS Theme-Präferenz")


def _ensure_dms_admin_role(admin: KeycloakAdmin) -> None:
    """Realm role for the first real role check in the entire system
    (P5e-S2, privileged reference number change in the Document Service) -
    created idempotently like the theme attribute above, `skip_exists=True`
    makes repetition harmless. Assignment to concrete users happens (for now)
    outside this service via the Keycloak Admin Console - a dedicated role
    management API/UI does not exist yet, see PROGRESS.md."""
    admin.create_realm_role(payload={"name": "dms-admin"}, skip_exists=True)


DOMAIN_ADMIN_USERS_USERNAME = "users-admin"
DOMAIN_ADMIN_CONFIG_USERNAME = "config-admin"

# Technical accounts per domain for which this session already wires up
# real enforcement (`(username, native permission-service role)`) -
# `users-admin`/"user/permission management" since P6-S5, `config-admin`/
# "object type/workflow configuration" since P6-S6 (workflow definitions/
# script task upload). The remaining 5 domains seeded in
# `permission_service.repository.DOMAIN_ADMIN_ROLES` deliberately don't get
# an account yet (see the rationale there). Since P18-S3 these accounts
# live as `TechnicalAccount` in this service's own DB instead of in
# Keycloak (see `domain_admins.py`) - hence no last-name field anymore,
# that was a pure Keycloak profile requirement.
DOMAIN_ADMIN_ACCOUNTS: list[tuple[str, str]] = [
    (DOMAIN_ADMIN_USERS_USERNAME, "domain-admin-users"),
    (DOMAIN_ADMIN_CONFIG_USERNAME, "domain-admin-config"),
]


_KERBEROS_FLOW_ALIAS = "dms-browser-kerberos"
_KERBEROS_COMPONENT_NAME = "dms-kerberos"


def _ensure_client_updated(admin: KeycloakAdmin, settings: Settings) -> None:
    """SSO/automatic login (post-roadmap feature) - fixes the `skip_exists`
    gap documented above for the SSO-relevant client fields: runs on EVERY
    startup (not just initial setup), enables the authorization code flow
    (`standardFlowEnabled`) and registers the redirect URIs of the allowed
    frontends. Runs INDEPENDENTLY of Kerberos - this is exactly the part
    that enables the redirect-to-Keycloak's-own-form fallback, even without
    Kerberos configured."""
    client_uuid = admin.get_client_id(settings.keycloak_client_id)
    if client_uuid is None:
        return
    redirect_uris = [
        f"{origin}/login/callback/" for origin in settings.sso_redirect_uri_allowed_origins
    ]
    admin.update_client(
        client_uuid, payload={"standardFlowEnabled": True, "redirectUris": redirect_uris}
    )


def _ensure_groups_mapper(admin: KeycloakAdmin, settings: Settings) -> None:
    """AD group -> internal role mapping (4.4, P24-S2): Keycloak does NOT
    automatically include group memberships in the access token (unlike
    roles via `realm_access.roles`) - the client needs an explicit
    `oidc-group-membership-mapper` for that. Without this claim,
    `ad_group_mapping.resolve_roles_for_groups` (see `main.py`'s `/me`)
    would always be a no-op, since `user.get("groups")` would always stay
    empty.

    Runs like `_ensure_client_updated` on EVERY startup (not just initial
    setup) - `create_client(..., skip_exists=True)` above would otherwise
    never add this mapper to an already-existing client (any installation
    predating this session), see `ensure_realm_and_client`'s "known
    limitation" docstring. `full.path=false` yields only the bare group
    name (e.g. `sales`), not a Keycloak-internal path (`/sales`) -
    consistent with this session's deliberately simple 1:1 name mapping
    (no hierarchical group matching)."""
    client_uuid = admin.get_client_id(settings.keycloak_client_id)
    if client_uuid is None:
        return
    existing_mappers = admin.get_mappers_from_client(client_uuid)
    if any(mapper.get("name") == "groups" for mapper in existing_mappers):
        return
    admin.add_mapper_to_client(
        client_uuid,
        payload={
            "name": "groups",
            "protocol": "openid-connect",
            "protocolMapper": "oidc-group-membership-mapper",
            "consentRequired": False,
            "config": {
                "full.path": "false",
                "id.token.claim": "false",
                "access.token.claim": "true",
                "userinfo.token.claim": "false",
                "claim.name": "groups",
            },
        },
    )


def _ensure_kerberos(admin: KeycloakAdmin, settings: Settings) -> None:
    """Kerberos/SPNEGO realm configuration (post-roadmap feature, SSO) - runs
    ONLY if all three Kerberos settings are set (if one is missing, SSO
    stays limited to the plain OIDC redirect-to-Keycloak's-form fallback,
    see `_ensure_client_updated` - no KDC/keytab requirement for a
    fresh/sandbox-like installation). Keycloak's built-in `browser` flow
    already ships with an `auth-spnego` execution (just disabled by
    default) - `copy_authentication_flow` duplicates the entire flow
    including this disabled execution (identical to the Keycloak Admin
    Console's "Duplicate"), here it is merely enabled instead of newly
    created (`create_authentication_flow_execution` would otherwise have
    produced a second, duplicate Kerberos stage)."""
    if not (
        settings.kerberos_enabled
        and settings.kerberos_realm
        and settings.kerberos_server_principal
        and settings.kerberos_keytab_path
    ):
        return

    existing_flows = admin.get_authentication_flows()
    if not any(flow.get("alias") == _KERBEROS_FLOW_ALIAS for flow in existing_flows):
        admin.copy_authentication_flow(
            payload={"newName": _KERBEROS_FLOW_ALIAS}, flow_alias="browser"
        )

    executions = admin.get_authentication_flow_executions(_KERBEROS_FLOW_ALIAS)
    kerberos_execution = next((e for e in executions if e.get("providerId") == "auth-spnego"), None)
    if kerberos_execution is not None and kerberos_execution.get("requirement") != "ALTERNATIVE":
        kerberos_execution["requirement"] = "ALTERNATIVE"
        admin.update_authentication_flow_executions(kerberos_execution, _KERBEROS_FLOW_ALIAS)

    admin.update_realm(settings.keycloak_realm, payload={"browserFlow": _KERBEROS_FLOW_ALIAS})

    # Keycloak's `RealmRepresentation.id` is (unlike for clients, which have
    # a separate internal UUID) identical to the realm name for the realm
    # itself - `parentId` of a realm-wide component is therefore directly
    # `settings.keycloak_realm`, no additional resolution needed.
    existing_components = admin.get_components(
        query={
            "parent": settings.keycloak_realm,
            "type": "org.keycloak.storage.UserStorageProvider",
        }
    )
    if not any(c.get("name") == _KERBEROS_COMPONENT_NAME for c in existing_components):
        admin.create_component(
            {
                "name": _KERBEROS_COMPONENT_NAME,
                "providerId": "kerberos",
                "providerType": "org.keycloak.storage.UserStorageProvider",
                "parentId": settings.keycloak_realm,
                # Keycloak's component config is `Map<String, List<String>>` -
                # every value MUST be a single-element list, not a bare
                # string.
                "config": {
                    "kerberosRealm": [settings.kerberos_realm],
                    "serverPrincipal": [settings.kerberos_server_principal],
                    "keyTab": [settings.kerberos_keytab_path],
                    "debug": ["false"],
                    "allowPasswordAuthentication": ["false"],
                    "editMode": ["UNSYNCED"],
                    "updateProfileFirstLogin": ["false"],
                    "useKerberosForPasswordAuthentication": ["false"],
                    "cachePolicy": ["DEFAULT"],
                },
            }
        )


def ensure_realm_and_client(settings: Settings) -> None:
    """Idempotent initial setup (analogous to the `CREATE SCHEMA IF NOT EXISTS`
    pattern of the other services): creates the realm and OIDC client if they
    don't already exist. Runs on every startup - ``skip_exists=True`` makes
    repetition harmless.

    Known limitation: if the client payload (e.g. the audience mapper below)
    is changed later, an already-existing client does not pick that up
    automatically - `skip_exists` skips creation entirely. Not critical for
    the dev/test environment (the realm is manually recreated when needed).
    """
    admin = KeycloakAdmin(
        server_url=settings.keycloak_base_url,
        username=settings.keycloak_admin_username,
        password=settings.keycloak_admin_password,
        realm_name="master",
        user_realm_name="master",
    )
    admin.create_realm(
        payload={"realm": settings.keycloak_realm, "enabled": True}, skip_exists=True
    )
    admin.change_current_realm(settings.keycloak_realm)
    admin.create_client(
        payload={
            "clientId": settings.keycloak_client_id,
            "secret": settings.keycloak_client_secret,
            "publicClient": False,
            "directAccessGrantsEnabled": True,
            "standardFlowEnabled": False,
            "serviceAccountsEnabled": False,
            "enabled": True,
            # Without an audience mapper, the access token only carries
            # "account" as aud (Keycloak default) - but TokenValidator checks
            # specifically against this service's own client name (4.4).
            "protocolMappers": [
                {
                    "name": "audience-mapper",
                    "protocol": "openid-connect",
                    "protocolMapper": "oidc-audience-mapper",
                    "consentRequired": False,
                    "config": {
                        "included.client.audience": settings.keycloak_client_id,
                        "id.token.claim": "false",
                        "access.token.claim": "true",
                    },
                }
            ],
        },
        skip_exists=True,
    )
    _ensure_theme_attribute(admin)
    _ensure_dms_admin_role(admin)
    _ensure_client_updated(admin, settings)
    _ensure_groups_mapper(admin, settings)
    _ensure_kerberos(admin, settings)
