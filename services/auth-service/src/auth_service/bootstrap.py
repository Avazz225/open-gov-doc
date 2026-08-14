from keycloak import KeycloakAdmin

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


def _ensure_dms_admin_role(admin: KeycloakAdmin) -> None:
    """Realm-Rolle für die erste echte Rollenprüfung im gesamten System
    (P5e-S2, privilegierte Kennzeichen-Änderung im Document Service) - idempotent
    angelegt wie das Theme-Attribut oben, `skip_exists=True` macht Wiederholung
    ungefährlich. Zuweisung an konkrete Nutzer erfolgt (vorerst) außerhalb dieses
    Service über die Keycloak Admin Console - eine eigene Rollenverwaltungs-API/
    -UI existiert noch nicht, siehe PROGRESS.md."""
    admin.create_realm_role(payload={"name": "dms-admin"}, skip_exists=True)


DOMAIN_ADMIN_USERS_USERNAME = "users-admin"
DOMAIN_ADMIN_CONFIG_USERNAME = "config-admin"

# Technische Konten je Domäne, für die diese Session bereits eine echte
# Durchsetzung verdrahtet (`(username, systemeigene Permission-Service-Rolle)`)
# - `users-admin`/"Nutzer-/Rechteverwaltung" seit P6-S5, `config-admin`/
# "Objekttyp-/Workflow-Konfiguration" seit P6-S6 (Workflow-Definitionen/
# Script-Task-Upload). Die übrigen 5 in
# `permission_service.repository.DOMAIN_ADMIN_ROLES` geseedeten Domänen
# bekommen bewusst noch kein Konto (siehe dortige Begründung). Seit P18-S3
# leben diese Konten als `TechnicalAccount` in der eigenen DB statt in
# Keycloak (siehe `domain_admins.py`) - daher kein Nachname-Feld mehr, das war
# reine Keycloak-Profil-Pflicht.
DOMAIN_ADMIN_ACCOUNTS: list[tuple[str, str]] = [
    (DOMAIN_ADMIN_USERS_USERNAME, "domain-admin-users"),
    (DOMAIN_ADMIN_CONFIG_USERNAME, "domain-admin-config"),
]


_KERBEROS_FLOW_ALIAS = "dms-browser-kerberos"
_KERBEROS_COMPONENT_NAME = "dms-kerberos"


def _ensure_client_updated(admin: KeycloakAdmin, settings: Settings) -> None:
    """SSO/automatischer Login (Post-Roadmap-Feature) - behebt die oben
    dokumentierte `skip_exists`-Lücke für die SSO-relevanten Client-Felder:
    läuft bei JEDEM Start (nicht nur bei Ersteinrichtung), aktiviert den
    Authorization-Code-Flow (`standardFlowEnabled`) und registriert die
    Redirect-URIs der erlaubten Frontends. Läuft UNABHÄNGIG von Kerberos -
    das ist genau der Teil, der den Redirect-zu-Keycloaks-eigenem-Formular-
    Fallback ermöglicht, auch ohne Kerberos konfiguriert."""
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
    """AD-Gruppe -> interne Rolle-Mapping (4.4, P24-S2): Keycloak trägt
    Gruppenmitgliedschaften NICHT automatisch in den Access-Token ein
    (anders als Rollen über `realm_access.roles`) - dafür braucht der Client
    einen expliziten `oidc-group-membership-mapper`. Ohne diesen Claim wäre
    `ad_group_mapping.resolve_roles_for_groups` (siehe `main.py`s `/me`)
    immer wirkungslos, da `user.get("groups")` stets leer bliebe.

    Läuft wie `_ensure_client_updated` bei JEDEM Start (nicht nur bei
    Ersteinrichtung) - `create_client(..., skip_exists=True)` oben würde
    einen bereits bestehenden Client (jede Installation vor dieser Session)
    sonst nie um diesen Mapper ergänzen, siehe `ensure_realm_and_client`s
    "Bekannte Grenze"-Docstring. `full.path=false` liefert nur den blanken
    Gruppennamen (z. B. `sales`), keinen Keycloak-internen Pfad
    (`/sales`) - passend zur bewusst einfachen 1:1-Namensabbildung dieser
    Session (kein hierarchisches Gruppen-Matching)."""
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
    """Kerberos/SPNEGO-Realmkonfiguration (Post-Roadmap-Feature, SSO) - läuft
    NUR, wenn alle drei Kerberos-Einstellungen gesetzt sind (fehlt eine,
    bleibt SSO auf den reinen OIDC-Redirect-zu-Keycloaks-Formular-Fallback
    beschränkt, siehe `_ensure_client_updated` - keine KDC/Keytab-Pflicht für
    eine frische/sandbox-artige Installation). Keycloaks eingebauter
    `browser`-Flow bringt eine `auth-spnego`-Ausführung bereits mit (nur
    standardmäßig deaktiviert) - `copy_authentication_flow` dupliziert den
    kompletten Flow inkl. dieser deaktivierten Ausführung (identisch zu
    Keycloak-Admin-Console-"Duplizieren"), hier wird sie nur aktiviert statt
    neu angelegt (`create_authentication_flow_execution` hätte sonst eine
    zweite, doppelte Kerberos-Stufe erzeugt)."""
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

    # Keycloaks `RealmRepresentation.id` ist (anders als bei Clients, die
    # eine separate interne UUID haben) für den Realm selbst identisch zum
    # Realm-Namen - `parentId` eines Realm-weiten Components ist deshalb
    # direkt `settings.keycloak_realm`, keine zusätzliche Auflösung nötig.
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
                # Keycloaks Component-Config ist `Map<String, List<String>>` -
                # jeder Wert MUSS eine Einzelelement-Liste sein, kein nackter
                # String.
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
    _ensure_dms_admin_role(admin)
    _ensure_client_updated(admin, settings)
    _ensure_groups_mapper(admin, settings)
    _ensure_kerberos(admin, settings)
