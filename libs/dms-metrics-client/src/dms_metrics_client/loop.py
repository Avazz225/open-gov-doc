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
    """Generischer Poll-Loop für "aktueller Zustand"-Sensoren (gleiches Idiom
    wie `plugin_orchestration_service.sampler`/`license_service`-Poll-Loops).
    Ruft je Tick nur die Sampler-Funktion eines Sensors auf, wenn dieser
    aktuell aktiv ist - bei Deaktivierung unterbleibt die (ggf. teure)
    Datenbankabfrage vollständig, nicht nur das Setzen des Gauges."""
    while True:
        for name, (gauge, compute) in samplers.items():
            if not gauge.is_active():
                continue
            try:
                gauge.set(await compute())
            except Exception:
                logger.exception("gauge_sample_tick_failed", extra={"sensor": name})
        await asyncio.sleep(interval_seconds)
