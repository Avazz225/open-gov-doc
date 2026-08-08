import time

import httpx


class AuthServiceClient:
    """Superuser-Status (4.6) - 1:1 Kopie des `license-service`-Musters."""

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
    `domain-admin-orchestration` (`admin.orchestration`) - 1:1 Kopie des
    `license-service`-Musters."""

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


class RegistryClient:
    """Fragt `registry-service`, ob mindestens eine gesunde Instanz eines
    `service_type` aktuell erreichbar ist - fuer den rein informativen
    Abhaengigkeits-Check bei `POST /placements` (3.8 nennt "Abhaengigkeiten
    zu anderen Services" als Manifest-Feld, aber kein blockierendes
    Verhalten). Kurzes TTL-Caching + Fail-Open (Verfuegbarkeit schlaegt eine
    kurzzeitig unerreichbare Registry), gleiches Muster wie andere
    Cross-Service-Clients im Projekt."""

    def __init__(self, base_url: str, *, cache_ttl_seconds: float) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, bool]] = {}

    async def has_healthy_instance(self, service_type: str) -> bool:
        now = time.monotonic()
        cached = self._cache.get(service_type)
        if cached is not None and now - cached[0] < self._cache_ttl:
            return cached[1]

        try:
            response = await self._client.get(f"/instances/{service_type}")
            response.raise_for_status()
            result = any(instance["healthy"] for instance in response.json())
        except httpx.HTTPError:
            result = cached[1] if cached is not None else False

        self._cache[service_type] = (now, result)
        return result

    async def close(self) -> None:
        await self._client.aclose()
