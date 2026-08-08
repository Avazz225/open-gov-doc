from registry_service.license_client import RawLicenseStatus
from registry_service.licensing import ComponentLicenseCache


class FakeLicenseClient:
    def __init__(self, status: RawLicenseStatus) -> None:
        self.status = status
        self.calls = 0

    async def get_status(self) -> RawLicenseStatus:
        self.calls += 1
        return self.status


async def test_core_service_is_always_licensed_without_calling_client():
    client = FakeLicenseClient(
        RawLicenseStatus(installed=False, valid=False, licensed_components=None)
    )
    cache = ComponentLicenseCache(client, licensable_components={}, cache_ttl_seconds=60.0)

    result = await cache.status_for("document-service")

    assert result == "licensed"
    assert client.calls == 0


async def test_licensable_component_without_installed_license_falls_back_to_demo_policy():
    client = FakeLicenseClient(
        RawLicenseStatus(installed=False, valid=False, licensed_components=None)
    )
    cache = ComponentLicenseCache(
        client, licensable_components={"workflow-service": "demo"}, cache_ttl_seconds=60.0
    )

    result = await cache.status_for("workflow-service")

    assert result == "demo"


async def test_licensable_component_with_lock_policy_returns_unlicensed():
    client = FakeLicenseClient(
        RawLicenseStatus(installed=True, valid=False, licensed_components=None)
    )
    cache = ComponentLicenseCache(
        client, licensable_components={"workflow-service": "lock"}, cache_ttl_seconds=60.0
    )

    result = await cache.status_for("workflow-service")

    assert result == "unlicensed"


async def test_valid_license_with_null_licensed_components_means_all_licensed():
    client = FakeLicenseClient(
        RawLicenseStatus(installed=True, valid=True, licensed_components=None)
    )
    cache = ComponentLicenseCache(
        client, licensable_components={"workflow-service": "demo"}, cache_ttl_seconds=60.0
    )

    result = await cache.status_for("workflow-service")

    assert result == "licensed"


async def test_valid_license_covering_component_returns_licensed():
    client = FakeLicenseClient(
        RawLicenseStatus(installed=True, valid=True, licensed_components=["workflow-service"])
    )
    cache = ComponentLicenseCache(
        client, licensable_components={"workflow-service": "demo"}, cache_ttl_seconds=60.0
    )

    assert await cache.status_for("workflow-service") == "licensed"


async def test_valid_license_not_covering_component_falls_back_to_policy():
    client = FakeLicenseClient(
        RawLicenseStatus(installed=True, valid=True, licensed_components=["document-service"])
    )
    cache = ComponentLicenseCache(
        client, licensable_components={"workflow-service": "demo"}, cache_ttl_seconds=60.0
    )

    assert await cache.status_for("workflow-service") == "demo"


async def test_cache_does_not_refetch_within_ttl():
    client = FakeLicenseClient(
        RawLicenseStatus(installed=True, valid=True, licensed_components=None)
    )
    cache = ComponentLicenseCache(
        client, licensable_components={"workflow-service": "demo"}, cache_ttl_seconds=60.0
    )

    await cache.status_for("workflow-service")
    await cache.status_for("workflow-service")

    assert client.calls == 1


async def test_invalidate_forces_refetch():
    client = FakeLicenseClient(
        RawLicenseStatus(installed=True, valid=True, licensed_components=None)
    )
    cache = ComponentLicenseCache(
        client, licensable_components={"workflow-service": "demo"}, cache_ttl_seconds=60.0
    )

    await cache.status_for("workflow-service")
    cache.invalidate()
    await cache.status_for("workflow-service")

    assert client.calls == 2
