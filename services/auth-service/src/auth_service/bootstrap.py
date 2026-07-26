from keycloak import KeycloakAdmin

from auth_service.settings import Settings


def ensure_realm_and_client(settings: Settings) -> None:
    """Idempotente Ersteinrichtung (analog zum `CREATE SCHEMA IF NOT EXISTS`-Muster
    der übrigen Services): legt Realm und OIDC-Client an, falls sie noch nicht
    existieren. Läuft bei jedem Start - ``skip_exists=True`` macht Wiederholung
    ungefährlich.

    Bekannte Grenze: Wird der Client-Payload (z. B. der Audience-Mapper unten)
    später geändert, zieht ein bereits bestehender Client das nicht automatisch
    nach - `skip_exists` überspringt die Erstellung komplett. Für die
    Dev-/Testumgebung unkritisch (Realm wird bei Bedarf manuell neu angelegt).
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
            # Ohne Audience-Mapper trägt der Access-Token nur "account" als aud
            # (Keycloak-Default) - TokenValidator prüft aber gezielt gegen den
            # eigenen Client-Namen (4.4).
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
