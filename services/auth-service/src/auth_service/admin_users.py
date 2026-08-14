from keycloak import KeycloakAdmin
from keycloak.exceptions import KeycloakDeleteError, KeycloakGetError, KeycloakPostError

from auth_service.settings import Settings


class UserAlreadyExistsError(Exception):
    pass


class UserNotFoundError(Exception):
    pass


def build_admin_client(settings: Settings) -> KeycloakAdmin:
    """A dedicated admin client fixed to the application realm (not
    `master`) for user management (4.4/8, Admin UI since P4-S3) - separate
    from the bootstrap usage in `bootstrap.py`, which operates across
    realms."""
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
    """Directory search (2.5/4.4, P15-S4) - unlike `list_users()`,
    deliberately WITHOUT the `admin.user_management` gate (see
    `find_user_by_username` for the same pattern); since P19-S3, however,
    gated via the "everyone" group from permission-service instead of being
    completely open (`main.py.search_directory`). Uses Keycloak's built-in
    `search` query parameter instead of a custom filter mechanism -
    identical behavior to the user search in Keycloak's own admin console
    UI. Confirmed via live verification (P15-S4): not true
    substring/infix matching, but a prefix match per field
    (username/first name/last name/email) - e.g. "config" or "conf" matches
    the username "config-admin", whereas "admin" (in the middle of the
    string) does not. Case-insensitive, implemented server-side in Keycloak
    itself."""
    return [_to_user_dict(u) for u in admin.get_users(query={"search": query})]


def find_user_by_username(admin: KeycloakAdmin, username: str) -> dict | None:
    """Exact name search for `GET /users/lookup` (2.5, P14-S6) - unlike
    `list_users()`, deliberately WITHOUT the `admin.user_management` gate;
    since P19-S3, however, gated via the "everyone" group from
    permission-service instead of being completely open
    (`main.py.lookup_user`): a single, exactly named account may be
    resolved (needed e.g. to invite someone into a teamspace by username -
    `X-DMS-Principal` is the Keycloak `sub` UUID, and no user knows another
    person's UUID by heart). Deliberately returns only `id`/`username`
    accordingly, no email/name/enabled status like `list_users()` - not a
    general directory function, see `docs/services/auth-service.md`."""
    matches = admin.get_users(query={"username": username, "exact": True})
    if not matches:
        return None
    return _to_user_dict(matches[0])


def find_user_by_id(admin: KeycloakAdmin, user_id: str) -> dict | None:
    """Reverse identity resolution for `GET /users/{user_id}` (Post-Roadmap
    Phase 19 Session 4, ADR 0069) - the counterpart to `find_user_by_username`
    above: `X-DMS-Principal`/`delegator_principal_id`/`principal_id` etc.
    are the Keycloak `sub` UUID everywhere in the system, and no user knows
    it by heart - frontends previously displayed these UUIDs raw
    (delegations, teamspace member lists), for lack of a reverse
    resolution. Deliberately returns only `id`/`username`, the same pattern
    as `find_user_by_username` - not a general directory function."""
    try:
        raw = admin.get_user(user_id)
    except KeycloakGetError as exc:
        if exc.response_code == 404:
            return None
        raise
    return _to_user_dict(raw)


def create_user(
    admin: KeycloakAdmin,
    *,
    username: str,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
) -> dict:
    """`emailVerified`+`firstName`/`lastName` are mandatory - without them,
    Keycloak 25's default user profile triggers "Account is not fully set
    up" on first login (the same trap as in `bootstrap.py`/the Auth Service
    tests)."""
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
    """Theme preference from a Keycloak user attribute (P4-S6, concept 8) -
    no new persistence component needed, since user accounts already live
    entirely in Keycloak (see `list_users`). Keycloak returns attributes as
    lists regardless of whether they are single- or multi-valued.
    """
    raw = admin.get_user(user_id)
    values = raw.get("attributes", {}).get(_THEME_ATTRIBUTE)
    return values[0] if values else "auto"


def set_theme_preference(admin: KeycloakAdmin, user_id: str, theme: str) -> None:
    """Merge attributes individually instead of overwriting - an update
    without existing attributes in the payload would otherwise delete them
    in Keycloak."""
    raw = admin.get_user(user_id)
    attributes = dict(raw.get("attributes", {}))
    attributes[_THEME_ATTRIBUTE] = [theme]
    admin.update_user(user_id, {"attributes": attributes})
