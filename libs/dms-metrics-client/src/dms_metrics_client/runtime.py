"""Runtime sensors every instrumented service gets (P88-S2b, package C of
the approved sensor catalog extension): requests in flight, database pool
usage, process memory/CPU/file descriptors and startup duration.

All of them are pull-on-scrape: read when `/metrics` is served and only
while the sensor is active (10.1) - no background loop per service."""

from __future__ import annotations

import os
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI

from dms_metrics_client.sensors import GuardedGauge, SensorRegistry, SensorSpec

HTTP_IN_FLIGHT = SensorSpec(
    name="http.requests.in_flight",
    group="performance",
    cost=1,
    description="Gerade laufende HTTP-Anfragen dieser Instanz",
)
DB_POOL_IN_USE = SensorSpec(
    name="db.pool.in_use",
    group="capacity",
    cost=1,
    description="Ausgeliehene Verbindungen des Datenbank-Pools",
)
DB_POOL_SIZE = SensorSpec(
    name="db.pool.size",
    group="capacity",
    cost=1,
    description="Groesse des Datenbank-Pools (ohne Overflow)",
)
PROCESS_MEMORY = SensorSpec(
    name="process.memory_bytes",
    group="runtime",
    cost=1,
    description="Belegter Arbeitsspeicher des Prozesses (RSS) in Bytes",
)
PROCESS_CPU = SensorSpec(
    name="process.cpu_seconds",
    group="runtime",
    cost=1,
    description="Verbrauchte CPU-Zeit des Prozesses (User + System) in Sekunden",
)
PROCESS_OPEN_FDS = SensorSpec(
    name="process.open_fds",
    group="runtime",
    cost=1,
    description="Offene Dateideskriptoren des Prozesses",
)
STARTUP_DURATION = SensorSpec(
    name="service.startup.duration_seconds",
    group="runtime",
    cost=1,
    description="Dauer des letzten Starts (Lifespan bis betriebsbereit) in Sekunden",
)

RUNTIME_SPECS = (
    HTTP_IN_FLIGHT,
    DB_POOL_IN_USE,
    DB_POOL_SIZE,
    PROCESS_MEMORY,
    PROCESS_CPU,
    PROCESS_OPEN_FDS,
    STARTUP_DURATION,
)

_PROC = "/proc/self"


def _memory_bytes() -> float | None:
    """RSS from /proc (Linux, which every container is); None elsewhere."""
    try:
        with open(f"{_PROC}/status") as status:
            for line in status:
                if line.startswith("VmRSS:"):
                    return float(line.split()[1]) * 1024
    except OSError:
        return None
    return None


def _cpu_seconds() -> float | None:
    try:
        times = os.times()
    except OSError:
        return None
    return times.user + times.system


def _open_fds() -> float | None:
    try:
        return float(len(os.listdir(f"{_PROC}/fd")))
    except OSError:
        return None


def _pool_stat(read: Callable[[object], int]) -> Callable[[], float | None]:
    """Sums a pool statistic over every engine this process built
    (`dms_db_base.live_engines`); None for a service without a database."""

    def sample() -> float | None:
        try:
            from dms_db_base import live_engines
        except ImportError:
            return None
        pools = [engine.sync_engine.pool for engine in live_engines()]
        values = [read(pool) for pool in pools if hasattr(pool, "checkedout")]
        return float(sum(values)) if values else None

    return sample


def build_runtime_sensors(registry: SensorRegistry) -> GuardedGauge:
    """Registers the runtime sensors and their samplers; returns the
    in-flight gauge for the HTTP middleware and the startup gauge is set by
    `instrument_startup`."""
    in_flight = registry.gauge(HTTP_IN_FLIGHT)
    registry.on_scrape(registry.gauge(DB_POOL_IN_USE), _pool_stat(lambda p: p.checkedout()))
    registry.on_scrape(registry.gauge(DB_POOL_SIZE), _pool_stat(lambda p: p.size()))
    registry.on_scrape(registry.gauge(PROCESS_MEMORY), _memory_bytes)
    registry.on_scrape(registry.gauge(PROCESS_CPU), _cpu_seconds)
    registry.on_scrape(registry.gauge(PROCESS_OPEN_FDS), _open_fds)
    return in_flight


def instrument_startup(app: FastAPI, registry: SensorRegistry) -> None:
    """Times the app's lifespan up to its `yield` - the moment the service
    is ready - by wrapping the router's lifespan context. Works at module
    level, before the app starts, like the HTTP middleware."""
    startup_gauge = registry.gauge(STARTUP_DURATION)
    original = app.router.lifespan_context

    @asynccontextmanager
    async def timed_lifespan(application) -> AsyncIterator[object]:
        started = time.monotonic()
        async with original(application) as state:
            startup_gauge.set(time.monotonic() - started)
            yield state

    app.router.lifespan_context = timed_lifespan


def runtime_sensor_declarations() -> list[dict]:
    return [spec.as_dict() for spec in RUNTIME_SPECS]
