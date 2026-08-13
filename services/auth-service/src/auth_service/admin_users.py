from keycloak import KeycloakAdmin
from keycloak.exceptions import KeycloakDeleteError, KeycloakPostError

from auth_service.settings import Settings


class UserAlreadyExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


def build_admin_client(settings: Settings) -> KeycloakAdmin:
    """Eigener, auf den Anwendungs-Realm (nicht `master`) fixierter Admin-Client
    für Nutzerverwaltung (4.4/8, Admin-UI seit P4-S3) - getrennt von der
    Bootstrap-Nutzung in `bootstrap.py`, die realmübergreifend arbeitet."""
    admin = KeycloakAdmin(
        server_url=settings.keycloak_base_url,
        username=settings.keycloak_admin_username,
        password=settings.keycloak_admin_password,
        realm_name="master",
        user_realm_name="master",
    )
    admin.change_current_realm(settings.keycloak_realm)
    return admin


def _to_user_dict(raw: dict) -> dict:
    return {
        "id": raw["id"],
        "username": raw["username"],
        "email": raw.get("email"),
        "enabled": raw.get("enabled", True),
        "first_name": raw.get("firstName"),
        "last_name": raw.get("lastName"),
    }


def list_users(admin: KeycloakAdmin) -> list[dict]:
    return [_to_user_dict(u) for u in admin.get_users()]


def search_users(admin: KeycloakAdmin, query: str) -> list[dict]:
    """Verzeichnis-Suche (2.5/4.4, P15-S4) - anders als `list_users()` bewusst
    OHNE `admin.user_management`-Gate (siehe `find_user_by_username` für
    dasselbe Muster), seit P19-S3 aber über die "everyone"-Gruppe aus
    permission-service gegated statt komplett offen (`main.py.search_
    directory`). Nutzt Keycloaks eingebauten `search`-Query-Parameter
    statt eines eigenen Filter-Mechanismus - identisches Verhalten wie die
    Nutzersuche im Keycloak-eigenen Admin-Konsolen-UI. Per Live-Verifikation
    (P15-S4) bestätigt: kein echtes Teilstring-/Infix-Matching, sondern ein
    Präfix-Treffer je Feld (Benutzername/Vor-/Nachname/E-Mail) - z. B. matcht
    "config" oder "conf" den Benutzernamen "config-admin", "admin" (mitten im
    String) hingegen nicht. Case-insensitive, serverseitig in Keycloak selbst
    umgesetzt."""
    return [_to_user_dict(u) for u in admin.get_users(query={"search": query})]


def find_user_by_username(admin: KeycloakAdmin, username: str) -> dict | None:
    """Exakte Namenssuche für `GET /users/lookup` (2.5, P14-S6) - anders als
    `list_users()` bewusst OHNE `admin.user_management`-Gate, seit P19-S3
    aber über die "everyone"-Gruppe aus permission-service gegated statt
    komplett offen (`main.py.lookup_user`): ein einzelner, exakt benannter
    Account darf aufgelöst werden (nötig, um z. B. jemanden per Username in
    einen Teamspace einzuladen - `X-DMS-Principal` ist die Keycloak-`sub`-
    UUID, kein Nutzer kennt die UUID einer anderen Person auswendig). Liefert
    deshalb auch bewusst nur `id`/`username` zurück, keine E-Mail/Namen/
    Freigabestatus wie `list_users()` - keine allgemeine Verzeichnis-
    Funktion, siehe `docs/services/auth-service.md`."""
    matches = admin.get_users(query={"username": username, "exact": True})
    if not matches:
        return None
    return _to_user_dict(matches[0])


def create_user(
    admin: KeycloakAdmin,
    *,
    username: str,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
) -> dict:
    """`emailVerified`+`firstName`/`lastName` sind Pflicht - ohne sie löst
    Keycloak 25s Default-User-Profile beim ersten Login "Account is not fully
    set up" aus (dieselbe Falle wie in `bootstrap.py`/den Auth-Service-Tests)."""
    try:
        user_id = admin.create_user(
            payload={
                "username": username,
                "email": email,
                "enabled": True,
                "emailVerified": True,
                "firstName": first_name,
                "lastName": last_name,
                "credentials": [{"type": "password", "value": password, "temporary": False}],
            },
            exist_ok=False,
        )
    except KeycloakPostError as exc:
        if exc.response_code == 409:
            raise UserAlreadyExistsError(f"Benutzername {username!r} existiert bereits") from exc
        raise
    return _to_user_dict(admin.get_user(user_id))


def delete_user(admin: KeycloakAdmin, user_id: str) -> None:
    try:
        admin.delete_user(user_id)
    except KeycloakDeleteError as exc:
        if exc.response_code == 404:
            raise UserNotFoundError(f"user_id {user_id!r} unbekannt") from exc
        raise


_THEME_ATTRIBUTE = "dms_theme"


def get_theme_preference(admin: KeycloakAdmin, user_id: str) -> str:
    """Theme-Präferenz aus einem Keycloak-Nutzerattribut (P4-S6, Konzept 8) -
    kein neuer Persistenz-Baustein nötig, da Nutzerkonten ohnehin vollständig
    in Keycloak leben (s. `list_users`). Keycloak liefert Attribute als
    Listen zurück, unabhängig davon, ob sie single- oder multi-valued sind.
    """
    raw = admin.get_user(user_id)
    values = raw.get("attributes", {}).get(_THEME_ATTRIBUTE)
    return values[0] if values else "auto"


def set_theme_preference(admin: KeycloakAdmin, user_id: str, theme: str) -> None:
    """Attribute einzeln zusammenführen statt zu überschreiben - ein Update
    ohne bestehende Attribute im Payload würde sie bei Keycloak sonst
    löschen."""
    raw = admin.get_user(user_id)
    attributes = dict(raw.get("attributes", {}))
    attributes[_THEME_ATTRIBUTE] = [theme]
    admin.update_user(user_id, {"attributes": attributes})
