from datetime import UTC, datetime

import pytest
from auth_service import superuser
from auth_service.models import TechnicalAccount
from sqlalchemy import select


@pytest.fixture(autouse=True)
async def _reset_superuser(session_factory):
    """Sorgt dafür, dass das Break-Glass-Konto nach jedem Test angelegt und
    wieder deaktiviert ist. `_clean_tables` (conftest, autouse) leert
    `technical_account` bereits vor jedem Test - das Konto muss hier also
    frisch angelegt werden, anders als zuvor bei Keycloak (das zwischen
    Testläufen persistierte)."""
    await superuser.ensure_superuser_account(session_factory)
    yield
    await superuser.deactivate(session_factory)


async def test_activate_enables_account_and_sets_expiry(session_factory):
    expires_at = await superuser.activate(session_factory, activation_minutes=30)

    active, stored_expires_at = await superuser.get_status(session_factory)
    assert active is True
    assert stored_expires_at == expires_at
    assert expires_at > datetime.now(UTC)


async def test_deactivate_disables_account(session_factory):
    await superuser.activate(session_factory, activation_minutes=30)

    await superuser.deactivate(session_factory)

    active, _ = await superuser.get_status(session_factory)
    assert active is False


async def test_get_status_when_never_activated(session_factory):
    active, expires_at = await superuser.get_status(session_factory)

    assert active is False
    assert expires_at is None


async def test_deactivate_if_expired_leaves_active_activation_untouched(session_factory):
    await superuser.activate(session_factory, activation_minutes=30)

    deactivated = await superuser.deactivate_if_expired(session_factory)

    assert deactivated is False
    active, _ = await superuser.get_status(session_factory)
    assert active is True


async def test_deactivate_if_expired_deactivates_past_activation(session_factory):
    await superuser.activate(session_factory, activation_minutes=-1)

    deactivated = await superuser.deactivate_if_expired(session_factory)

    assert deactivated is True
    active, _ = await superuser.get_status(session_factory)
    assert active is False


async def test_deactivate_if_expired_is_noop_when_not_active(session_factory):
    assert await superuser.deactivate_if_expired(session_factory) is False


async def test_get_principal_id_returns_the_technical_account_id(session_factory):
    """Grundlage für den P6-S6-Cross-Service-Check: permission-service muss
    verifizieren können, dass ein `maintenance-mode/lift`-Aufrufer wirklich
    der aktive Superuser ist."""
    principal_id = await superuser.get_principal_id(session_factory)

    assert principal_id is not None
    async with session_factory() as session:
        account = await session.scalar(
            select(TechnicalAccount).where(
                TechnicalAccount.username == superuser.SUPERUSER_USERNAME
            )
        )
    assert principal_id == str(account.id)


async def test_ensure_superuser_account_is_idempotent(session_factory):
    await superuser.ensure_superuser_account(session_factory)

    async with session_factory() as session:
        accounts = (
            await session.scalars(
                select(TechnicalAccount).where(
                    TechnicalAccount.username == superuser.SUPERUSER_USERNAME
                )
            )
        ).all()
    assert len(accounts) == 1
    assert accounts[0].enabled is False
    assert accounts[0].account_type == "superuser"
