import logging
import time
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

LicenseComponentStatus = Literal["licensed", "demo", "unlicensed"]


class LicenseStatusClient:
    """Fragt den eigenen Lizenzstatus beim `registry-service` ab (Konzept
    9.3, P9-S2) - nicht direkt beim `license-service`, die Registry ist
    einzige Vermittlungsstelle. Lazy-TTL-Cache, Vorbild
    `permission_client.PermissionServiceClient` (gleicher fail-open-Ansatz:
    ein nicht erreichbarer registry-service soll workflow-service nicht
    komplett lahmlegen)."""

    def __init__(
        self,
        registry_base_url: str,
        service_type: str,
        cache_ttl_seconds: float,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=registry_base_url, timeout=10.0, transport=transport
        )
        self._service_type = service_type
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cached_status: LicenseComponentStatus = "licensed"
        self._cached_at: float | None = None

    async def get_status(self) -> LicenseComponentStatus:
        now = time.monotonic()
        if self._cached_at is not None and now - self._cached_at < self._cache_ttl_seconds:
            return self._cached_status
        try:
            response = await self._client.get(f"/license-status/{self._service_type}")
            response.raise_for_status()
            self._cached_status = response.json()["status"]
        except httpx.HTTPError:
            logger.warning("workflow_license_status_check_failed")
        self._cached_at = now
        return self._cached_status

    async def close(self) -> None:
        await self._client.aclose()
