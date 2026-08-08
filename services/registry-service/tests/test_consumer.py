from registry_service.consumer import make_handler
from registry_service.license_client import RawLicenseStatus
from registry_service.licensing import ComponentLicenseCache


class FakeLicenseClient:
    def __init__(self, status: RawLicenseStatus) -> None:
        self.status = status

    async def get_status(self) -> RawLicenseStatus:
        return self.status


async def test_any_license_event_invalidates_the_cache():
    client = FakeLicenseClient(
        RawLicenseStatus(installed=True, valid=True, licensed_components=None)
    )
    cache = ComponentLicenseCache(
        client, licensable_components={"workflow-service": "demo"}, cache_ttl_seconds=60.0
    )
    await cache.status_for("workflow-service")
    assert cache._cached_at is not None

    handler = make_handler(cache)
    await handler(b"irrelevant-payload")

    assert cache._cached_at is None
