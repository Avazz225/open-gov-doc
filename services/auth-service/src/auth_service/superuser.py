from datetime import UTC, datetime, timedelta

from keycloak import KeycloakAdmin

SUPERUSER_USERNAME = "superuser"
EXPIRES_AT_ATTRIBUTE = "dms_superuser_expires_at"


class SuperuserNotConfiguredError(Exception):
    pass


def ensure_superuser_account(admin: KeycloakAdmin) -> None:
    """Break-Glass-Konto (4.6): idempotent angelegt, **immer** `enabled=False`
    nach Ersteinrichtung - Reaktivierung geschieht ausschließlich über
    `activate()` unten (Konsument des genehmigten Vier-Augen-Requests, siehe
    `consumer.py`), nie durch direktes Keycloak-Update von Hand vorgesehen.
    Default-Passwort = Username, wie bei den Domain-Admin-Konten - sollte vom
    Betreiber geändert werden."""
    if admin.get_users(query={"username": SUPERUSER_USERNAME, "exact": True}):
        return
    admin.create_user(
        payload={
            "username": SUPERUSER_USERNAME,
            "email": f"{SUPERUSER_USERNAME}@system.local",
            "enabled": False,
            "emailVerified": True,
            "firstName": "Superuser",
            "lastName": "Break-Glass",
            "credentials": [{"type": "password", "value": SUPERUSER_USERNAME, "temporary": False}],
        },
        exist_ok=True,
    )


def _get_superuser_id(admin: KeycloakAdmin) -> str:
    users = admin.get_users(query={"username": SUPERUSER_USERNAME, "exact": True})
    if not users:
        raise SuperuserNotConfiguredError("Superuser-Konto wurde noch nicht angelegt")
    return users[0]["id"]


def activate(admin: KeycloakAdmin, *, activation_minutes: int) -> datetime:
    """Aktiviert das Konto zeitlich befristet (4.6) - ein einziger absoluter
    Ablauf-Zeitstempel statt getrennter Gesamtdauer-/Inaktivitäts-Timer
    (bewusste Vereinfachung, siehe ADR 0023), als Keycloak-Attribut abgelegt
    statt in einer neuen Datenbank (gleiches Muster wie die Theme-Präferenz,
    ADR 0009) - `auth-service` bleibt dadurch weiterhin zustandslos."""
    user_id = _get_superuser_id(admin)
    expires_at = datetime.now(UTC) + timedelta(minutes=activation_minutes)
    raw = admin.get_user(user_id)
    attributes = dict(raw.get("attributes", {}))
    attributes[EXPIRES_AT_ATTRIBUTE] = [expires_at.isoformat()]
    admin.update_user(user_id, {"enabled": True, "attributes": attributes})
    return expires_at


def deactivate(admin: KeycloakAdmin) -> None:
    user_id = _get_superuser_id(admin)
    raw = admin.get_user(user_id)
    attributes = dict(raw.get("attributes", {}))
    attributes.pop(EXPIRES_AT_ATTRIBUTE, None)
    admin.update_user(user_id, {"enabled": False, "attributes": attributes})


def get_status(admin: KeycloakAdmin) -> tuple[bool, datetime | None]:
    """`(active, expires_at)` - `active` liest den tatsächlichen Keycloak-
    `enabled`-Status, nicht nur den Zeitstempel, da `deactivate()`/der
    Poll-Loop `enabled=False` setzen, sobald abgelaufen."""
    user_id = _get_superuser_id(admin)
    raw = admin.get_user(user_id)
    values = raw.get("attributes", {}).get(EXPIRES_AT_ATTRIBUTE)
    expires_at = datetime.fromisoformat(values[0]) if values else None
    return bool(raw.get("enabled", False)), expires_at


def deactivate_if_expired(admin: KeycloakAdmin) -> bool:
    """Wird vom Poll-Loop (`main.py`) periodisch aufgerufen (ADR 0020-Muster) -
    gibt True zurück, wenn tatsächlich deaktiviert wurde (für den Event-Publish
    im Aufrufer)."""
    active, expires_at = get_status(admin)
    if not active or expires_at is None:
        return False
    if datetime.now(UTC) < expires_at:
        return False
    deactivate(admin)
    return True
