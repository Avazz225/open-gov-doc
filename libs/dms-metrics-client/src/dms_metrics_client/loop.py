from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from dms_metrics_client.sensors import GuardedGauge

logger = logging.getLogger(__name__)


async def run_gauge_sampler_loop(
    samplers: dict[str, tuple[GuardedGauge, Callable[[], Awaitable[float]]]],
    *,
    interval_seconds: float,
) -> None:
    """Generic poll loop for "current state" sensors (same idiom as
    `plugin_orchestration_service.sampler`/`license_service` poll loops).
    Calls a sensor's sampler function per tick only if it is currently
    active - when deactivated, the (possibly expensive) database query is
    skipped entirely, not just the setting of the gauge."""
    while True:
        for name, (gauge, compute) in samplers.items():
            if not gauge.is_active():
                continue
            try:
                gauge.set(await compute())
            except Exception:
                logger.exception("gauge_sample_tick_failed", extra={"sensor": name})
        await asyncio.sleep(interval_seconds)
