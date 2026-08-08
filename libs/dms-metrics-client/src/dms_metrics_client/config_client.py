from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx

logger = logging.getLogger(__name__)


class SensorConfigClient:
    """Pollt die Sensor-Aktivierungskonfiguration beim `monitoring-service`
    (`GET /sensor-config`) - TTL-Poll statt NATS-Invalidierung (bewusste
    P11-S0/S1-Scope-Entscheidung: Pilot statt Vollausbau). Fail-open: bleibt
    bei einem unerreichbaren `monitoring-service` auf dem zuletzt bekannten
    Stand, vor der allerersten erfolgreichen Abfrage auf "alles aktiv" -
    ein Monitoring-Ausfall soll die Sensor-Erfassung selbst niemals
    blockieren (Konzept 10.1: Monitoring ist ein Zusatznutzen, kein
    Hard-Dependency für den fachlichen Betrieb).

    `is_active()` ist bewusst synchron (liest nur den zuletzt gepollten
    Cache) - die `Guarded*`-Sensor-Wrapper in `sensors.py` rufen sie direkt
    aus synchronem Code auf, ohne selbst async werden zu müssen.
    """

    def __init__(
        self,
        base_url: str,
        *,
        poll_interval_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=5.0)
        self._poll_interval = poll_interval_seconds
        self._global_default = True
        self._overrides: dict[str, bool] = {}
        self._task: asyncio.Task | None = None

    def is_active(self, name: str) -> bool:
        return self._overrides.get(name, self._global_default)

    async def _refresh_once(self) -> None:
        try:
            response = await self._client.get("/sensor-config")
            response.raise_for_status()
            body = response.json()
            self._global_default = body["global_default"]
            self._overrides = body["overrides"]
        except httpx.HTTPError:
            logger.warning("sensor_config_refresh_failed")

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._poll_interval)
            await self._refresh_once()

    async def start(self) -> None:
        await self._refresh_once()
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        await self._client.aclose()
