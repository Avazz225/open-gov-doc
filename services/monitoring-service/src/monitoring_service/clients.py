import httpx


class AuthServiceClient:
    """Superuser-Status (4.6) - 1:1 Kopie des `license-service`/
    `plugin-orchestration-service`-Musters."""

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
    """Gate-Client fuer die domaenengetrennte Admin-Rolle
    `domain-admin-monitoring` (`admin.monitoring`) - 1:1 Kopie des
    `license-service`/`plugin-orchestration-service`-Musters."""

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
    """Liest die aktuell aktiven, gesunden Instanzen aller Servicetypen bei
    `registry-service` (`GET /instances`) - sowohl als Scrape-Zielliste
    (`scraper.scrape_and_merge`) als auch als Quelle für den aggregierten
    Sensor-Katalog (`repository.list_sensors`). Kein neuer Endpunkt in
    `registry-service` nötig (P11-S1-Entscheidung: das bereits vorhandene
    `sensors`-Feld auf `InstanceOut` reicht)."""

    def __init__(self, base_url: str, *, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url, timeout=10.0)

    async def list_active_instances(self) -> list[RegistryInstance]:
        """Nur Instanzen mit mindestens einem deklarierten Sensor - die
        meisten registrierten Services haben (noch) keinen `/metrics`-
        Endpunkt (kein Vollretrofit, P11-S0-Befund); ein Scrape-Versuch
        gegen sie wäre ein strukturell erwarteter 404, kein echter
        Ausfall, und würde `monitoring_scrape_failures_total` mit
        Falschmeldungen fluten."""
        response = await self._client.get("/instances")
        response.raise_for_status()
        return [
            RegistryInstance(i["instance_id"], i["service_type"], i["address"], i["sensors"])
            for i in response.json()
            if i["healthy"] and i["status"] == "active" and i.get("sensors")
        ]

    async def close(self) -> None:
        await self._client.aclose()
