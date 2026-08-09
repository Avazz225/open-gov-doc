import logging
import time
from typing import Literal

import httpx

logger = logging.getLogger(__name__)

LicenseComponentStatus = Literal["licensed", "demo", "unlicensed"]


class LicenseStatusClient:
    """Bewusst synchron (`httpx.Client`, nicht `AsyncClient`) - anders als bei
    `workflow_service.license_client.LicenseStatusClient` wird dieser Client
    aus `dav_provider.py`s durchgehend synchronen wsgidav-Callback-Methoden
    heraus aufgerufen (siehe `dms_connector_sdk.DmsTreeClient`s Docstring für
    dieselbe Begründung). Identisches Verhalten wie das async Original
    (P9-S2): Lazy-TTL-Cache, fail-open auf `"licensed"`."""

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
            logger.warning("webdav_connector_license_status_check_failed")
        self._cached_at = now
        return self._cached_status

    def close(self) -> None:
        self._client.close()
