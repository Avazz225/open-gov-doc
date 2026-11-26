from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable

import httpx

from dms_metrics_client.cost import LEGACY_KEY_LEVELS, normalize_cost

logger = logging.getLogger(__name__)

# Override keys besides a plain sensor name (P88-S2, ADR 0243): a whole
# sensor group, or every sensor of one cost class (concept 10.1: "switch
# off expensive sensors during continuous operation").
GROUP_PREFIX = "group:"
COST_PREFIX = "cost:"


def override_key_for_group(group: str) -> str:
    return f"{GROUP_PREFIX}{group}"


def override_key_for_cost(cost: int | str) -> str:
    return f"{COST_PREFIX}{cost}"


def resolve_active(
    global_default: bool,
    overrides: dict[str, bool],
    name: str,
    *,
    group: str | None = None,
    cost: int | str | None = None,
    default_enabled: bool = True,
) -> tuple[bool, str]:
    """The one resolution rule, shared by every service's sensors and by
    monitoring-service's catalog: the most specific setting wins - the
    sensor's own override, then its group's, then its cost level's
    (`cost:<1-5>`), then the default. The default is the global setting,
    except for a sensor declared `default_enabled=False` (P88-S2c), which
    stays off until an override turns it on.

    Returns the value and where it came from (`sensor`/`group`/`cost`/
    `global`/`default` - the last for a default-off sensor left off)."""
    if name in overrides:
        return overrides[name], "sensor"
    if group is not None and override_key_for_group(group) in overrides:
        return overrides[override_key_for_group(group)], "group"
    if cost is not None:
        level = normalize_cost(cost)
        key = override_key_for_cost(level)
        if key in overrides:
            return overrides[key], "cost"
        # A not-yet-migrated legacy key (monitoring-service rewrites them
        # to levels on startup and on write, so this is only a safety net).
        for legacy, levels in LEGACY_KEY_LEVELS.items():
            legacy_key = override_key_for_cost(legacy)
            if level in levels and legacy_key in overrides:
                return overrides[legacy_key], "cost"
    if not default_enabled:
        return False, "default"
    return global_default, "global"


# Tracing sampling ratio (P94-S1, ADR 0250): monitoring-service hands it out
# with the sensor configuration. `dms_common.otel.setup_tracing` registers the
# process's sampler here; without tracing nobody listens.
_tracing_ratio_listeners: list[Callable[[float], None]] = []


def register_tracing_ratio_listener(listener: Callable[[float], None]) -> None:
    if listener not in _tracing_ratio_listeners:
        _tracing_ratio_listeners.append(listener)


def _notify_tracing_ratio(ratio) -> None:
    if ratio is None:
        return
    for listener in list(_tracing_ratio_listeners):
        try:
            listener(float(ratio))
        except Exception:
            logger.warning("tracing_ratio_listener_failed", exc_info=True)


# Log levels (P97-S1, ADR 0267) travel the same way: `dms_common.logging`
# registers a listener that applies them to this process's loggers.
_log_levels_listeners: list[Callable[[dict], None]] = []


def register_log_levels_listener(listener: Callable[[dict], None]) -> None:
    if listener not in _log_levels_listeners:
        _log_levels_listeners.append(listener)


def _notify_log_levels(config) -> None:
    # Absent (an older monitoring-service) leaves the levels as they are.
    if not isinstance(config, dict):
        return
    for listener in list(_log_levels_listeners):
        try:
            listener(config)
        except Exception:
            logger.warning("log_levels_listener_failed", exc_info=True)


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

    def is_active(
        self,
        name: str,
        *,
        group: str | None = None,
        cost: int | str | None = None,
        default_enabled: bool = True,
    ) -> bool:
        return resolve_active(
            self._global_default,
            self._overrides,
            name,
            group=group,
            cost=cost,
            default_enabled=default_enabled,
        )[0]

    async def _refresh_once(self) -> None:
        try:
            response = await self._client.get("/sensor-config")
            response.raise_for_status()
            body = response.json()
            self._global_default = body["global_default"]
            self._overrides = body["overrides"]
            _notify_tracing_ratio(body.get("tracing_sampling_ratio"))
            _notify_log_levels(body.get("log_levels"))
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

    def is_active(
        self,
        name: str,
        *,
        group: str | None = None,
        cost: int | str | None = None,
        default_enabled: bool = True,
    ) -> bool:
        # Unbound (before startup, in tests): everything on, except sensors
        # that are off by default - those are never switched on implicitly.
        if self._client is None:
            return default_enabled
        return self._client.is_active(name, group=group, cost=cost, default_enabled=default_enabled)
