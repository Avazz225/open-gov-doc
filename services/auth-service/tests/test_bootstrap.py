from auth_service.bootstrap import (
    _KERBEROS_FLOW_ALIAS,
    DOMAIN_ADMIN_CONFIG_USERNAME,
    DOMAIN_ADMIN_USERS_USERNAME,
    ensure_realm_and_client,
)
from auth_service.settings import Settings
from auth_service.superuser import SUPERUSER_USERNAME

settings = Settings()


def test_bootstrap_is_idempotent(keycloak_admin):
    ensure_realm_and_client(settings)
    ensure_realm_and_client(settings)  # zweiter Aufruf darf nicht scheitern

    assert keycloak_admin.get_realm(settings.keycloak_realm)["realm"] == settings.keycloak_realm
    assert keycloak_admin.get_client_id(settings.keycloak_client_id) is not None


def test_settings_defaults():
    s = Settings(_env_file=None)
    assert s.service_name == "auth-service"
    assert s.keycloak_realm == "dms"


def test_bootstrap_creates_dms_admin_role_idempotently(keycloak_admin):
    ensure_realm_and_client(settings)
    ensure_realm_and_client(settings)  # zweiter Aufruf darf nicht scheitern

    assert keycloak_admin.get_realm_role("dms-admin")["name"] == "dms-admin"


def test_bootstrap_creates_domain_admin_account_idempotently(keycloak_admin):
    ensure_realm_and_client(settings)
    ensure_realm_and_client(settings)  # zweiter Aufruf darf nicht scheitern

    users = keycloak_admin.get_users(query={"username": DOMAIN_ADMIN_USERS_USERNAME, "exact": True})
    assert len(users) == 1
    assert users[0]["enabled"] is True


def test_bootstrap_creates_config_admin_account_idempotently(keycloak_admin):
    """Zweite Domäne mit echtem Konto (P6-S6, Workflow-Konfiguration) -
    gleiches Muster wie users-admin."""
    ensure_realm_and_client(settings)
    ensure_realm_and_client(settings)  # zweiter Aufruf darf nicht scheitern

    users = keycloak_admin.get_users(
        query={"username": DOMAIN_ADMIN_CONFIG_USERNAME, "exact": True}
    )
    assert len(users) == 1
    assert users[0]["enabled"] is True


def test_bootstrap_creates_superuser_account_disabled_by_default(keycloak_admin):
    """Löscht ein evtl. von einem anderen Test aktiviertes Konto zuerst, um
    unabhängig von der Testreihenfolge zu prüfen, dass eine frische
    Erstellung immer mit `enabled=False` startet (4.6)."""
    existing = keycloak_admin.get_users(query={"username": SUPERUSER_USERNAME, "exact": True})
    for user in existing:
        keycloak_admin.delete_user(user["id"])

    ensure_realm_and_client(settings)

    users = keycloak_admin.get_users(query={"username": SUPERUSER_USERNAME, "exact": True})
    assert len(users) == 1
    assert users[0]["enabled"] is False


def test_client_update_sets_standard_flow_and_redirect_uris(keycloak_admin):
    """SSO/automatischer Login (Post-Roadmap-Feature) - `_ensure_client_
    updated` behebt die `skip_exists`-Lücke: läuft bei JEDEM Start, nicht nur
    bei Ersteinrichtung, muss also auch bei einem bereits existierenden
    Client die SSO-relevanten Felder setzen."""
    ensure_realm_and_client(settings)
    ensure_realm_and_client(settings)  # zweiter Aufruf darf nicht scheitern

    client_uuid = keycloak_admin.get_client_id(settings.keycloak_client_id)
    client = keycloak_admin.get_client(client_uuid)
    assert client["standardFlowEnabled"] is True
    assert client["redirectUris"] == [
        f"{origin}/login/callback/" for origin in settings.sso_redirect_uri_allowed_origins
    ]


def test_kerberos_disabled_skips_cleanly(keycloak_admin):
    """Ohne alle drei Kerberos-Einstellungen bleibt der Realm auf dem
    Standard-`browser`-Flow - `_ensure_kerberos` legt dann bewusst nichts an
    (siehe Docstring dort). Räumt zuerst einen evtl. von einem anderen
    Testlauf übrig gebliebenen Kerberos-Flow weg, damit dieser Test
    unabhängig von der Reihenfolge/Historie ist."""
    for flow in keycloak_admin.get_authentication_flows():
        if flow.get("alias") == _KERBEROS_FLOW_ALIAS:
            keycloak_admin.delete_authentication_flow(flow["id"])
    keycloak_admin.update_realm(settings.keycloak_realm, payload={"browserFlow": "browser"})

    disabled_settings = Settings(_env_file=None, kerberos_enabled=False)
    ensure_realm_and_client(disabled_settings)

    flows = keycloak_admin.get_authentication_flows()
    assert not any(flow.get("alias") == _KERBEROS_FLOW_ALIAS for flow in flows)
    assert keycloak_admin.get_realm(settings.keycloak_realm)["browserFlow"] == "browser"


def test_kerberos_enabled_creates_flow_execution_and_component(keycloak_admin):
    """Bei aktiviertem Kerberos (auch mit einem nicht existierenden Keytab-
    Pfad - Keycloak prüft die Datei erst bei der tatsächlichen SPNEGO-
    Verhandlung, nicht beim Anlegen der Komponente) entstehen Flow,
    SPNEGO-Ausführung (`requirement=ALTERNATIVE`), Realm-Verweis und
    Kerberos-Komponente korrekt - inklusive Idempotenz beim zweiten Lauf."""
    enabled_settings = Settings(
        _env_file=None,
        kerberos_enabled=True,
        kerberos_realm="DMS.TEST",
        kerberos_server_principal="HTTP/dms.test@DMS.TEST",
        kerberos_keytab_path="/nonexistent/dms.keytab",
    )
    try:
        ensure_realm_and_client(enabled_settings)
        ensure_realm_and_client(enabled_settings)  # zweiter Aufruf darf nicht scheitern

        flows = keycloak_admin.get_authentication_flows()
        assert any(flow.get("alias") == _KERBEROS_FLOW_ALIAS for flow in flows)

        executions = keycloak_admin.get_authentication_flow_executions(_KERBEROS_FLOW_ALIAS)
        spnego = next(e for e in executions if e.get("providerId") == "auth-spnego")
        assert spnego["requirement"] == "ALTERNATIVE"

        assert (
            keycloak_admin.get_realm(settings.keycloak_realm)["browserFlow"] == _KERBEROS_FLOW_ALIAS
        )

        components = keycloak_admin.get_components(
            query={
                "parent": settings.keycloak_realm,
                "type": "org.keycloak.storage.UserStorageProvider",
            }
        )
        assert any(c.get("name") == "dms-kerberos" for c in components)
    finally:
        # Aufräumen (Projektkonvention, siehe CONTRIBUTING.md) - sonst bleibt
        # der Realm dauerhaft auf dem Kerberos-Flow stehen und verfälscht
        # `test_kerberos_disabled_skips_cleanly` bei jedem künftigen Lauf.
        keycloak_admin.update_realm(settings.keycloak_realm, payload={"browserFlow": "browser"})
        for component in keycloak_admin.get_components(
            query={
                "parent": settings.keycloak_realm,
                "type": "org.keycloak.storage.UserStorageProvider",
            }
        ):
            if component.get("name") == "dms-kerberos":
                keycloak_admin.delete_component(component["id"])
        for flow in keycloak_admin.get_authentication_flows():
            if flow.get("alias") == _KERBEROS_FLOW_ALIAS:
                keycloak_admin.delete_authentication_flow(flow["id"])
