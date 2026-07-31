from datetime import UTC, datetime

import pytest
from auth_service import superuser


@pytest.fixture(autouse=True)
def _reset_superuser(keycloak_admin):
    """Sorgt dafür, dass das Break-Glass-Konto nach jedem Test wieder
    deaktiviert ist - Keycloak persistiert zwischen Testläufen, ein aktiviertes
    Konto darf nicht in andere Tests durchsickern."""
    superuser.ensure_superuser_account(keycloak_admin)
    yield
    superuser.deactivate(keycloak_admin)


def test_activate_enables_account_and_sets_expiry(keycloak_admin):
    expires_at = superuser.activate(keycloak_admin, activation_minutes=30)

    active, stored_expires_at = superuser.get_status(keycloak_admin)
    assert active is True
    assert stored_expires_at == expires_at
    assert expires_at > datetime.now(UTC)


def test_deactivate_disables_account(keycloak_admin):
    superuser.activate(keycloak_admin, activation_minutes=30)

    superuser.deactivate(keycloak_admin)

    active, _ = superuser.get_status(keycloak_admin)
    assert active is False


def test_get_status_when_never_activated(keycloak_admin):
    active, expires_at = superuser.get_status(keycloak_admin)

    assert active is False
    assert expires_at is None


def test_deactivate_if_expired_leaves_active_activation_untouched(keycloak_admin):
    superuser.activate(keycloak_admin, activation_minutes=30)

    deactivated = superuser.deactivate_if_expired(keycloak_admin)

    assert deactivated is False
    active, _ = superuser.get_status(keycloak_admin)
    assert active is True


def test_deactivate_if_expired_deactivates_past_activation(keycloak_admin):
    superuser.activate(keycloak_admin, activation_minutes=-1)

    deactivated = superuser.deactivate_if_expired(keycloak_admin)

    assert deactivated is True
    active, _ = superuser.get_status(keycloak_admin)
    assert active is False


def test_deactivate_if_expired_is_noop_when_not_active(keycloak_admin):
    assert superuser.deactivate_if_expired(keycloak_admin) is False
