from keycloak import KeycloakAdmin

from auth_service import superuser
from auth_service.settings import Settings


def _declare_profile_attribute(admin: KeycloakAdmin, *, name: str, display_name: str) -> None:
    """Gemeinsamer Deklarations-Helfer (P6-S5, extrahiert aus der vorherigen
    Theme-spezifischen Funktion) - Keycloaks Declarative User Profile (seit
    Keycloak 25 Default) verwirft jedes nicht deklarierte Attribut bei
    `update_user` still, kein Fehler, einfach kein Effekt."""
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
    """Theme-Präferenz (8, P4-S6). Ohne diese Deklaration würde
    `admin_users.set_theme_preference` klaglos ins Leere laufen."""
    _declare_profile_attribute(admin, name="dms_theme", display_name="DMS Theme-Präferenz")


def _ensure_superuser_expires_at_attribute(admin: KeycloakAdmin) -> None:
    """Break-Glass-Ablaufzeitpunkt (4.6, P6-S5) - gleiches Deklarationsmuster
    wie oben, sonst würde `superuser.activate()` klaglos ins Leere laufen."""
    _declare_profile_attribute(
        admin,
        name=superuser.EXPIRES_AT_ATTRIBUTE,
        display_name="DMS Superuser Break-Glass Ablaufzeitpunkt",
    )


def _ensure_dms_admin_role(admin: KeycloakAdmin) -> None:
    """Realm-Rolle für die erste echte Rollenprüfung im gesamten System
    (P5e-S2, privilegierte Kennzeichen-Änderung im Document Service) - idempotent
    angelegt wie das Theme-Attribut oben, `skip_exists=True` macht Wiederholung
    ungefährlich. Zuweisung an konkrete Nutzer erfolgt (vorerst) außerhalb dieses
    Service über die Keycloak Admin Console - eine eigene Rollenverwaltungs-API/
    -UI existiert noch nicht, siehe PROGRESS.md."""
    admin.create_realm_role(payload={"name": "dms-admin"}, skip_exists=True)


DOMAIN_ADMIN_USERS_USERNAME = "users-admin"


def _ensure_domain_admin_account(admin: KeycloakAdmin) -> None:
    """Technisches Konto für die Domäne "Nutzer-/Rechteverwaltung" (4.6,
    P6-S5) - Username=Passwort nach dem Muster ``<domain>-admin``, vom
    Betreiber zu ändern. Die zugehörige Rolle lebt bewusst NICHT in Keycloak,
    sondern systemeigen im Permission Service (siehe `permission_client.py`) -
    dieser Schritt legt nur das Konto an; die Rollenzuweisung erfolgt separat
    (async, gegen den Permission Service) im Lifespan von `main.py`, da
    `KeycloakAdmin` synchron ist, ein HTTP-Aufruf gegen einen anderen Service
    aber nicht."""
    if admin.get_users(query={"username": DOMAIN_ADMIN_USERS_USERNAME, "exact": True}):
        return
    admin.create_user(
        payload={
            "username": DOMAIN_ADMIN_USERS_USERNAME,
            "email": f"{DOMAIN_ADMIN_USERS_USERNAME}@system.local",
            "enabled": True,
            "emailVerified": True,
            "firstName": "Domain-Admin",
            "lastName": "Nutzerverwaltung",
            "credentials": [
                {
                    "type": "password",
                    "value": DOMAIN_ADMIN_USERS_USERNAME,
                    "temporary": False,
                }
            ],
        },
        exist_ok=True,
    )


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
    _ensure_theme_attribute(admin)
    _ensure_superuser_expires_at_attribute(admin)
    _ensure_dms_admin_role(admin)
    superuser.ensure_superuser_account(admin)
    _ensure_domain_admin_account(admin)
