import httpx
import pytest
from workflow_service.license_client import LicenseStatusClient


@pytest.fixture(autouse=True)
def _default_licensed():
    """Ueberschreibt (gleicher Name, engerer Scope) den globalen Autouse-Patch
    aus conftest.py - diese Tests pruefen genau das reale Verhalten von
    `LicenseStatusClient.get_status`, das sonst ueberall sonst absichtlich
    auf "licensed" festgenagelt wird."""
    yield


def _transport(status_code: int, body: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


async def test_get_status_returns_registry_response():
    client = LicenseStatusClient(
        "http://registry.local",
        "workflow-service",
        cache_ttl_seconds=60.0,
        transport=_transport(200, {"service_type": "workflow-service", "status": "demo"}),
    )

    assert await client.get_status() == "demo"
    await client.close()


async def test_get_status_caches_within_ttl():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={"service_type": "workflow-service", "status": "licensed"})

    client = LicenseStatusClient(
        "http://registry.local",
        "workflow-service",
        cache_ttl_seconds=60.0,
        transport=httpx.MockTransport(handler),
    )

    await client.get_status()
    await client.get_status()

    assert calls["count"] == 1
    await client.close()


async def test_get_status_fails_open_to_licensed_on_error():
    client = LicenseStatusClient(
        "http://registry.local",
        "workflow-service",
        cache_ttl_seconds=60.0,
        transport=_transport(500),
    )

    assert await client.get_status() == "licensed"
    await client.close()
