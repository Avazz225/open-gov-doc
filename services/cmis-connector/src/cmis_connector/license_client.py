import logging
import time
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

LicenseComponentStatus = Literal["licensed", "demo", "unlicensed"]


class LicenseStatusClient:
    """Identical behavior to `webdav_connector.license_client` (P12-S1,
    P9-S2 pattern) - deliberately synchronous, since FastAPI already runs
    regular `def` endpoints (not `async def`) automatically in their own
    threadpool anyway (see ADR 0033/0036)."""

    def __init__(self, registry_base_url: str, service_type: str, cache_ttl_seconds: float) -> None:
        self._client = httpx.Client(base_url=registry_base_url, timeout=10.0)
        self._service_type = service_type
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cached_status: LicenseComponentStatus = "licensed"
        self._cached_at: float | None = None

    def get_status(self) -> LicenseComponentStatus:
        now = time.monotonic()
        if self._cached_at is not None and now - self._cached_at < self._cache_ttl_seconds:
            return self._cached_status
        try:
            response = self._client.get(f"/license-status/{self._service_type}")
            response.raise_for_status()
            self._cached_status = response.json()["status"]
        except httpx.HTTPError:
            logger.warning("cmis_connector_license_status_check_failed")
        self._cached_at = now
        return self._cached_status

    def close(self) -> None:
        self._client.close()
