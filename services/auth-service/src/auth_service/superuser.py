from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from auth_service.local_token_issuer import hash_password
from auth_service.models import TechnicalAccount

SUPERUSER_USERNAME = "superuser"


class SuperuserNotConfiguredError(Exception):
    pass


async def ensure_superuser_account(session_factory) -> None:
    """Break-Glass-Konto (4.6): idempotent angelegt, **immer** `enabled=False`
    nach Ersteinrichtung - Reaktivierung geschieht ausschließlich über
    `activate()` unten (Konsument des genehmigten Vier-Augen-Requests, siehe
    `consumer.py`), nie durch direktes Update von Hand vorgesehen.
    Default-Passwort = Username, wie zuvor beim Keycloak-Konto - sollte vom
    Betreiber geändert werden (noch kein Passwort-Änderungs-Endpunkt, siehe
    "Offene Punkte").

    Auth-Entkopplung von Keycloak (Post-Roadmap-Feature, Phase 18, ADR 0063):
    lebt seit dieser Session als `TechnicalAccount`-Zeile statt eines
    Keycloak-Nutzerkontos - Break-Glass funktioniert dadurch unabhängig von
    Keycloaks Erreichbarkeit, der eigentliche Zweck eines
    Notfallmechanismus."""
    async with session_factory() as session:
        existing = await session.scalar(
            select(TechnicalAccount).where(TechnicalAccount.username == SUPERUSER_USERNAME)
        )
        if existing is not None:
            return
        session.add(
            TechnicalAccount(
                username=SUPERUSER_USERNAME,
                password_hash=hash_password(SUPERUSER_USERNAME),
                account_type="superuser",
                role_name=None,
                enabled=False,
                expires_at=None,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def _get_superuser_account(session) -> TechnicalAccount:
    account = await session.scalar(
        select(TechnicalAccount).where(TechnicalAccount.username == SUPERUSER_USERNAME)
    )
    if account is None:
        raise SuperuserNotConfiguredError("Superuser-Konto wurde noch nicht angelegt")
    return account


async def activate(session_factory, *, activation_minutes: int) -> datetime:
    """Aktiviert das Konto zeitlich befristet (4.6) - ein einziger absoluter
    Ablauf-Zeitstempel statt getrennter Gesamtdauer-/Inaktivitäts-Timer
    (bewusste Vereinfachung, siehe ADR 0023), seit Phase 18 als Spalten der
    eigenen `technical_account`-Zeile statt eines Keycloak-Attributs."""
    async with session_factory() as session:
        account = await _get_superuser_account(session)
        expires_at = datetime.now(UTC) + timedelta(minutes=activation_minutes)
        account.enabled = True
        account.expires_at = expires_at
        await session.commit()
        return expires_at


async def deactivate(session_factory) -> None:
    async with session_factory() as session:
        account = await _get_superuser_account(session)
        account.enabled = False
        account.expires_at = None
        await session.commit()


async def get_status(session_factory) -> tuple[bool, datetime | None]:
    """`(active, expires_at)` - `active` liest den tatsächlichen `enabled`-
    Wert, nicht nur den Zeitstempel, da `deactivate()`/der Poll-Loop
    `enabled=False` setzen, sobald abgelaufen."""
    async with session_factory() as session:
        account = await _get_superuser_account(session)
        return account.enabled, account.expires_at


async def get_principal_id(session_factory) -> str | None:
    """Stabile ID des Superuser-Kontos (`TechnicalAccount.id` als String) -
    nötig, damit `permission-service` (4.8, P6-S6) prüfen kann, ob ein
    `POST /maintenance-mode/lift`-Aufrufer tatsächlich der aktive Superuser
    ist (Vergleich gegen denselben Wert, der auch als `sub`-Claim in dessen
    Tokens landet, siehe `main.py._login_technical_account`). `None`, falls
    das Konto noch nicht angelegt wurde, statt zu werfen - der Aufrufer
    behandelt das wie "kein aktiver Superuser"."""
    async with session_factory() as session:
        account = await session.scalar(
            select(TechnicalAccount).where(TechnicalAccount.username == SUPERUSER_USERNAME)
        )
        return str(account.id) if account is not None else None


async def deactivate_if_expired(session_factory) -> bool:
    """Wird vom Poll-Loop (`main.py`) periodisch aufgerufen (ADR 0020-Muster) -
    gibt True zurück, wenn tatsächlich deaktiviert wurde (für den Event-Publish
    im Aufrufer)."""
    active, expires_at = await get_status(session_factory)
    if not active or expires_at is None:
        return False
    if datetime.now(UTC) < expires_at:
        return False
    await deactivate(session_factory)
    return True
