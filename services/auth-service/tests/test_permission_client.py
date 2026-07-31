import uuid

import pytest
from auth_service.permission_client import PermissionServiceClient, RoleNotFoundError
from auth_service.settings import Settings

settings = Settings()


@pytest.fixture
async def client():
    c = PermissionServiceClient(settings.permission_service_base_url)
    yield c
    await c.close()


async def test_has_permission_reflects_domain_admin_users_role(client):
    """Echter Aufruf gegen den laufenden `permission-service` (kein Mocking,
    gleiches Prinzip wie P6-S4s Cross-Service-Tests) - `domain-admin-users`
    wird von `permission-service` selbst beim Start geseedet (P6-S5)."""
    principal_id = f"test-{uuid.uuid4().hex[:8]}"

    before = await client.has_permission(principal_id, "admin.user_management")
    assert before is False

    await client.ensure_role_assignment(principal_id=principal_id, role_name="domain-admin-users")

    after = await client.has_permission(principal_id, "admin.user_management")
    assert after is True


async def test_ensure_role_assignment_is_idempotent(client):
    principal_id = f"test-{uuid.uuid4().hex[:8]}"

    await client.ensure_role_assignment(principal_id=principal_id, role_name="domain-admin-users")
    await client.ensure_role_assignment(principal_id=principal_id, role_name="domain-admin-users")

    assert await client.has_permission(principal_id, "admin.user_management") is True


async def test_ensure_role_assignment_with_unknown_role_raises(client):
    with pytest.raises(RoleNotFoundError):
        await client.ensure_role_assignment(principal_id="someone", role_name="does-not-exist")
