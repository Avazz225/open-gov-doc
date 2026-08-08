import logging
import time
from typing import Literal

import httpx

from registry_service.license_client import LicenseServiceClient

logger = logging.getLogger(__name__)

LicenseComponentStatus = Literal["licensed", "demo", "unlicensed"]


class ComponentLicenseCache:
    """Vermittelt den Lizenzstatus je `service_type` (Konzept 9.3 3.2b).

    Nur Services, die in `licensable_components` gelistet sind, gelten
    ueberhaupt als separat lizenzierbare "Applikationskomponenten" (9.1) -
    jeder andere `service_type` ist "core" und bekommt immer `"licensed"`.
    Fuer eine licensierbare Komponente greift die konfigurierte Policy
    (`"demo"`/`"lock"`), wenn keine gueltige Lizenz installiert ist oder die
    Komponente nicht in `licensed_components` der Lizenz enthalten ist.

    TTL-Cache (Vorbild `gateway_service.upstream.MaintenanceStateClient`) plus
    Invalidierung durch den `license.>`-NATS-Konsumenten - reagiert damit
    sowohl ereignisgetrieben als auch mit einer harten Obergrenze auf
    Statusaenderungen, ohne bei jeder Anfrage einen Aufruf zu machen.
    """

    def __init__(
        self,
        client: LicenseServiceClient,
        *,
        licensable_components: dict[str, str],
        cache_ttl_seconds: float,
    ) -> None:
        self._client = client
        self._licensable_components = licensable_components
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cached_at: float | None = None
        self._cached_installed = False
        self._cached_valid = False
        self._cached_licensed_components: list[str] | None = None

    def invalidate(self) -> None:
        self._cached_at = None

    async def close(self) -> None:
        await self._client.close()

    async def _refresh_if_stale(self) -> None:
        now = time.monotonic()
        if self._cached_at is not None and now - self._cached_at < self._cache_ttl_seconds:
            return
        try:
            status = await self._client.get_status()
            self._cached_installed = status.installed
            self._cached_valid = status.valid
            self._cached_licensed_components = status.licensed_components
        except httpx.HTTPError:
            logger.warning("license_status_fetch_failed")
        self._cached_at = now

    async def status_for(self, service_type: str) -> LicenseComponentStatus:
        policy = self._licensable_components.get(service_type)
        if policy is None:
            return "licensed"

        await self._refresh_if_stale()

        if not self._cached_installed or not self._cached_valid:
            return "unlicensed" if policy == "lock" else "demo"

        if self._cached_licensed_components is None:
            return "licensed"
        if service_type in self._cached_licensed_components:
            return "licensed"
        return "unlicensed" if policy == "lock" else "demo"
