import httpx


class AuthServiceClient:
    """Superuser status (4.6) - 1:1 copy of the `license-service`/
    `plugin-orchestration-service` pattern."""

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def get_active_superuser(self) -> tuple[bool, str | None]:
        response = await self._client.get("/superuser/status")
        response.raise_for_status()
        body = response.json()
        return body["active"], body.get("principal_id")

    async def close(self) -> None:
        await self._client.aclose()


class PermissionServiceClient:
    """Gate client for the domain-separated admin role
    `domain-admin-monitoring` (`admin.monitoring`) - 1:1 copy of the
    `license-service`/`plugin-orchestration-service` pattern."""

    ROOT_RESOURCE_ID = "root"

    def __init__(self, base_url: str) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def has_permission(self, principal_id: str, permission: str) -> bool:
        response = await self._client.get(
            f"/effective-permissions/{principal_id}/{self.ROOT_RESOURCE_ID}"
        )
        response.raise_for_status()
        return permission in response.json()["permissions"]

    async def close(self) -> None:
        await self._client.aclose()


class RegistryInstance:
    def __init__(
        self, instance_id: str, service_type: str, address: str, sensors: list[dict]
    ) -> None:
        self.instance_id = instance_id
        self.service_type = service_type
        self.address = address
        self.sensors = sensors


class RegistryClient:
    """Reads the currently active, healthy instances of all service types from
    `registry-service` (`GET /instances`) - both as the scrape target list
    (`scraper.scrape_and_merge`) and as the source for the aggregated
    sensor catalog (`repository.list_sensors`). No new endpoint in
    `registry-service` needed (P11-S1 decision: the already existing
    `sensors` field on `InstanceOut` is sufficient)."""

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def list_active_instances(self) -> list[RegistryInstance]:
        """Only instances with at least one declared sensor - most
        registered services do not (yet) have a `/metrics`
        endpoint (no full retrofit, P11-S0 finding); a scrape attempt
        against them would be a structurally expected 404, not a real
        failure, and would flood `monitoring_scrape_failures_total` with
        false alarms."""
        response = await self._client.get("/instances")
        response.raise_for_status()
        return [
            RegistryInstance(i["instance_id"], i["service_type"], i["address"], i["sensors"])
            for i in response.json()
            if i["healthy"] and i["status"] == "active" and i.get("sensors")
        ]

    async def close(self) -> None:
        await self._client.aclose()
