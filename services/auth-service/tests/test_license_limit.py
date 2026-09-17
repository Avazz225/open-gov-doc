import uuid

import httpx
import pytest
from auth_service.license_client import LicenseLimitClient


@pytest.fixture(autouse=True)
def _default_no_license_limit_exceeded():
    """Overrides (same name, narrower scope) the global autouse patch from
    conftest.py - these tests check the real behavior of
    `LicenseLimitClient.is_exceeded` itself, same pattern as
    `document-service`'s identically named override."""
    yield


def _user_payload(**overrides):
    suffix = uuid.uuid4().hex[:8]
    payload = {
        "username": f"license-test-{suffix}",
        "email": f"license-test-{suffix}@example.com",
        "password": "testpass123",
        "first_name": "License",
        "last_name": "Test",
    }
    payload.update(overrides)
    return payload


def _transport(limits_exceeded: list[str]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"installed": True, "valid": True, "limits_exceeded": limits_exceeded}
        )

    return httpx.MockTransport(handler)


async def test_is_exceeded_true_when_dimension_listed():
    client = LicenseLimitClient(
        "http://license.local", cache_ttl_seconds=60.0, transport=_transport(["users"])
    )
    assert await client.is_exceeded("users") is True
    await client.close()


async def test_is_exceeded_false_when_dimension_not_listed():
    client = LicenseLimitClient(
        "http://license.local", cache_ttl_seconds=60.0, transport=_transport(["storage_gb"])
    )
    assert await client.is_exceeded("users") is False
    await client.close()


async def test_is_exceeded_fails_open_on_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = LicenseLimitClient(
        "http://license.local", cache_ttl_seconds=60.0, transport=httpx.MockTransport(handler)
    )
    assert await client.is_exceeded("users") is False
    await client.close()


def test_create_user_blocked_when_users_limit_exceeded(
    client, domain_admin_auth_headers, monkeypatch
):
    """Post-Roadmap Phase 42 Session 2 - brings "users" to parity with
    document-service's existing "documents"/"storage_gb" blocking."""

    async def _exceeded(self, dimension: str) -> bool:
        return dimension == "users"

    monkeypatch.setattr(LicenseLimitClient, "is_exceeded", _exceeded)

    response = client.post("/users", json=_user_payload(), headers=domain_admin_auth_headers)

    assert response.status_code == 403


def test_create_user_allowed_when_users_limit_not_exceeded(
    client, domain_admin_auth_headers, monkeypatch
):
    async def _not_exceeded(self, dimension: str) -> bool:
        return False

    monkeypatch.setattr(LicenseLimitClient, "is_exceeded", _not_exceeded)

    payload = _user_payload()
    response = client.post("/users", json=payload, headers=domain_admin_auth_headers)

    assert response.status_code == 201
    client.delete(f"/users/{response.json()['id']}", headers=domain_admin_auth_headers)
