from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from auth_service.local_token_issuer import hash_password
from auth_service.models import TechnicalAccount

SUPERUSER_USERNAME = "superuser"


class SuperuserNotConfiguredError(Exception):
    pass


async def ensure_superuser_account(session_factory) -> None:
    """Break-glass account (4.6): created idempotently, **always**
    `enabled=False` after initial setup - reactivation happens exclusively
    via `activate()` below (consumer of the approved four-eyes request, see
    `consumer.py`), never intended via a direct manual update.
    Default password = username, as before with the Keycloak account -
    should be changed by the operator (no password change endpoint yet,
    see "Open Points").

    Auth decoupling from Keycloak (post-roadmap feature, Phase 18, ADR
    0063): has lived as a `TechnicalAccount` row instead of a Keycloak user
    account since this session - break-glass thereby works independently
    of Keycloak's reachability, the actual purpose of an emergency
    mechanism."""
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
    """Activates the account for a limited time (4.6) - a single absolute
    expiry timestamp instead of separate total-duration/inactivity timers
    (deliberate simplification, see ADR 0023), as columns of this
    service's own `technical_account` row instead of a Keycloak attribute
    since Phase 18."""
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
    """`(active, expires_at)` - `active` reads the actual `enabled` value,
    not just the timestamp, since `deactivate()`/the poll loop set
    `enabled=False` as soon as it expires."""
    async with session_factory() as session:
        account = await _get_superuser_account(session)
        return account.enabled, account.expires_at


async def get_principal_id(session_factory) -> str | None:
    """Stable ID of the superuser account (`TechnicalAccount.id` as a
    string) - needed so `permission-service` (4.8, P6-S6) can check whether
    a `POST /maintenance-mode/lift` caller is actually the active superuser
    (compared against the same value that also ends up as the `sub` claim
    in its tokens, see `main.py._login_technical_account`). `None` if the
    account hasn't been created yet, instead of raising - the caller treats
    this as "no active superuser"."""
    async with session_factory() as session:
        account = await session.scalar(
            select(TechnicalAccount).where(TechnicalAccount.username == SUPERUSER_USERNAME)
        )
        return str(account.id) if account is not None else None


async def deactivate_if_expired(session_factory) -> bool:
    """Called periodically by the poll loop (`main.py`) (ADR 0020 pattern) -
    returns True if it was actually deactivated (for the event publish in
    the caller)."""
    active, expires_at = await get_status(session_factory)
    if not active or expires_at is None:
        return False
    if datetime.now(UTC) < expires_at:
        return False
    await deactivate(session_factory)
    return True
