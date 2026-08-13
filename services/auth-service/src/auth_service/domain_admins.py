from datetime import UTC, datetime

from sqlalchemy import select

from auth_service.local_token_issuer import hash_password
from auth_service.models import TechnicalAccount


async def ensure_domain_admin_account(session_factory, *, username: str, role_name: str) -> None:
    """Domänen-Admin-Konto (4.6, seit P6-S5/P6-S6, seit P18-S3 als
    `TechnicalAccount` statt Keycloak-Nutzer): idempotent angelegt,
    **immer** `enabled=True` - anders als der Superuser (`superuser.py`) kein
    Break-Glass-Mechanismus, sondern ein dauerhaft nutzbares technisches
    Konto für die jeweilige Domäne. Default-Passwort = Username, wie zuvor
    beim Keycloak-Konto - sollte vom Betreiber geändert werden (noch kein
    Passwort-Änderungs-Endpunkt, siehe "Offene Punkte")."""
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
    """Stabile ID eines technischen Kontos (`TechnicalAccount.id` als String)
    - genutzt beim Lifespan-Start, um die Rollenzuweisung gegen
    `permission-service` mit der `principal_id` durchzuführen, die auch als
    `sub`-Claim in dessen Tokens landet (siehe
    `main.py._mint_technical_account_tokens`). `None`, falls das Konto noch
    nicht angelegt wurde - der Aufrufer behandelt das wie "noch nicht
    bereit", identisch zum bisherigen Keycloak-`next(... )`-Fallback."""
    async with session_factory() as session:
        account = await session.scalar(
            select(TechnicalAccount).where(TechnicalAccount.username == username)
        )
        return str(account.id) if account is not None else None
