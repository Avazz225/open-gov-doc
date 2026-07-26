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
