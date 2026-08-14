from datetime import UTC, datetime

from sqlalchemy import select

from auth_service.local_token_issuer import hash_password
from auth_service.models import TechnicalAccount


async def ensure_domain_admin_account(session_factory, *, username: str, role_name: str) -> None:
    """Domain admin account (4.6, since P6-S5/P6-S6, as a `TechnicalAccount`
    instead of a Keycloak user since P18-S3): created idempotently,
    **always** `enabled=True` - unlike the superuser (`superuser.py`), no
    break-glass mechanism, but a permanently usable technical account for
    the respective domain. Default password = username, as before with the
    Keycloak account - should be changed by the operator (no password change
    endpoint yet, see "Open Points")."""
    async with session_factory() as session:
        existing = await session.scalar(
            select(TechnicalAccount).where(TechnicalAccount.username == username)
        )
        if existing is not None:
            return
        session.add(
            TechnicalAccount(
                username=username,
                password_hash=hash_password(username),
                account_type="domain-admin",
                role_name=role_name,
                enabled=True,
                expires_at=None,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def get_technical_account_id(session_factory, username: str) -> str | None:
    """Stable ID of a technical account (`TechnicalAccount.id` as a string)
    - used at lifespan startup to perform the role assignment against
    `permission-service` with the `principal_id` that also ends up as the
    `sub` claim in its tokens (see
    `main.py._mint_technical_account_tokens`). `None` if the account has not
    been created yet - the caller treats this as "not yet ready", identical
    to the previous Keycloak `next(...)` fallback."""
    async with session_factory() as session:
        account = await session.scalar(
            select(TechnicalAccount).where(TechnicalAccount.username == username)
        )
        return str(account.id) if account is not None else None
