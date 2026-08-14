from __future__ import annotations

import asyncio
import contextlib
import logging

import httpx

logger = logging.getLogger(__name__)


class SensorConfigClient:
    """Polls the sensor activation configuration from `monitoring-service`
    (`GET /sensor-config`) - TTL poll instead of NATS invalidation (deliberate
    P11-S0/S1 scope decision: pilot instead of full rollout). Fail-open:
    stays at the last known state when `monitoring-service` is unreachable,
    defaulting to "everything active" before the very first successful
    query - a monitoring outage should never block sensor collection itself
    (concept 10.1: monitoring is an added benefit, not a hard dependency for
    business operation).

    `is_active()` is deliberately synchronous (only reads the last polled
    cache) - the `Guarded*` sensor wrappers in `sensors.py` call it directly
    from synchronous code, without needing to become async themselves.
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
