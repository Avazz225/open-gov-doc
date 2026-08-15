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


class SensorConfigProxy:
    """Indirection between a module-level `SensorRegistry` (built once, at
    import time - required for `bootstrap_http_sensors`, since FastAPI
    forbids adding middleware once the app has started, see its docstring)
    and a `SensorConfigClient` that must be constructed fresh inside
    `lifespan` on every startup, not reused across restarts.

    A `SensorConfigClient` owns an `httpx.AsyncClient`, whose connection
    pool binds to whichever asyncio event loop first uses it and breaks
    ("Cannot send a request, as the client has been closed") once that loop
    is gone - normal in production (one loop, one process lifetime) but
    fatal for tests, where each `TestClient(app)` `with` block runs its own
    fresh event loop through the same, module-cached `app`. Binding the
    registry directly to one `SensorConfigClient` instance's `is_active`
    would tie every sensor permanently to that instance's first event loop.
    The registry instead binds to `proxy.is_active` once; `lifespan`
    `bind()`s a fresh client on each startup and `unbind()`s it on
    shutdown - fail-open (`True`) while unbound, same as
    `SensorConfigClient` before its first successful poll."""

    def __init__(self) -> None:
        self._client: SensorConfigClient | None = None

    def bind(self, client: SensorConfigClient) -> None:
        self._client = client

    def unbind(self) -> None:
        self._client = None

    def is_active(self, name: str) -> bool:
        if self._client is None:
            return True
        return self._client.is_active(name)
