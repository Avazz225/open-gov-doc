import logging
import time

import httpx

logger = logging.getLogger(__name__)


class LicenseLimitClient:
    """Fragt beim `license-service` (P9-S1) ab, ob eine Nutzungsdimension
    aktuell überschritten ist (Konzept 9.3: "verhindert neue Anlagen").
    Lazy-TTL-Cache, fail-open (nicht überschritten) bei nicht erreichbarem
    license-service - eine seltene Randbedingung soll nicht das gesamte
    Hochladen blockieren."""

    def __init__(
        self,
        base_url: str,
        cache_ttl_seconds: float,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(base_url=base_url, timeout=10.0, transport=transport)
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cached_limits_exceeded: list[str] = []
        self._cached_at: float | None = None

    async def is_exceeded(self, dimension: str) -> bool:
        now = time.monotonic()
        if self._cached_at is None or now - self._cached_at >= self._cache_ttl_seconds:
            try:
                response = await self._client.get("/license/status")
                response.raise_for_status()
                self._cached_limits_exceeded = response.json().get("limits_exceeded", [])
            except httpx.HTTPError:
                logger.warning("document_license_limit_check_failed")
                self._cached_limits_exceeded = []
            self._cached_at = now
        return dimension in self._cached_limits_exceeded

    async def close(self) -> None:
        await self._client.aclose()
