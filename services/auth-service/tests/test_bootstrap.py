from auth_service.bootstrap import ensure_realm_and_client
from auth_service.settings import Settings

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
