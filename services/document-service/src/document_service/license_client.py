import logging
import time

import httpx

logger = logging.getLogger(__name__)


class LicenseLimitClient:
    """Queries the `license-service` (P9-S1) whether a usage dimension
    is currently exceeded (concept 9.3: "prevents new items"). Lazy TTL
    cache, fail-open (not exceeded) if license-service is unreachable - a
    rare edge case should not block the entire upload."""

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
