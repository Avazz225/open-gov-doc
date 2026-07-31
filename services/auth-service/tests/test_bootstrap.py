from auth_service.bootstrap import DOMAIN_ADMIN_USERS_USERNAME, ensure_realm_and_client
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
