from auth_service import domain_admins
from auth_service.models import TechnicalAccount
from sqlalchemy import select


async def test_ensure_domain_admin_account_creates_enabled_account(session_factory):
    await domain_admins.ensure_domain_admin_account(
        session_factory, username="users-admin", role_name="domain-admin-users"
    )

    async with session_factory() as session:
        account = await session.scalar(
            select(TechnicalAccount).where(TechnicalAccount.username == "users-admin")
        )
    assert account is not None
    assert account.enabled is True
    assert account.account_type == "domain-admin"
    assert account.role_name == "domain-admin-users"


async def test_ensure_domain_admin_account_is_idempotent(session_factory):
    await domain_admins.ensure_domain_admin_account(
        session_factory, username="config-admin", role_name="domain-admin-config"
    )
    await domain_admins.ensure_domain_admin_account(
        session_factory, username="config-admin", role_name="domain-admin-config"
    )

    async with session_factory() as session:
        accounts = (
            await session.scalars(
                select(TechnicalAccount).where(TechnicalAccount.username == "config-admin")
            )
        ).all()
    assert len(accounts) == 1


async def test_get_technical_account_id_returns_none_when_missing(session_factory):
    assert await domain_admins.get_technical_account_id(session_factory, "no-such-account") is None


async def test_get_technical_account_id_returns_the_account_id(session_factory):
    await domain_admins.ensure_domain_admin_account(
        session_factory, username="users-admin", role_name="domain-admin-users"
    )

    account_id = await domain_admins.get_technical_account_id(session_factory, "users-admin")

    assert account_id is not None
    async with session_factory() as session:
        account = await session.scalar(
            select(TechnicalAccount).where(TechnicalAccount.username == "users-admin")
        )
    assert account_id == str(account.id)
