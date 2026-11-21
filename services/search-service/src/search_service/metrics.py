"""search-service sensors (P88-S2b).
- `search.index.failures` (package B): indexing is driven by NATS events and
  has no queue table; a failed reindex is only logged and redelivered by
  JetStream, so a counter is the one place failures become visible.
- `search.query.duration_seconds` (package C, expensive): the database
  search itself, without the permission filtering and HTTP overhead the
  generic `http.request.duration_seconds` includes."""

from __future__ import annotations

import time
from collections.abc import Awaitable

from dms_metrics_client import GuardedCounter, GuardedHistogram, SensorRegistry, SensorSpec

INDEX_FAILURES = SensorSpec(
    name="search.index.failures",
    group="reliability",
    cost="cheap",
    description=(
        "Fehlgeschlagene Indexierungen (Event wird von NATS erneut zugestellt) seit Prozessstart"
    ),
)
QUERY_DURATION = SensorSpec(
    name="search.query.duration_seconds",
    group="performance",
    cost="expensive",
    description="Dauer der Volltext-/Attributsuche in der Datenbank in Sekunden",
)

_failures: GuardedCounter | None = None
_query: GuardedHistogram | None = None


def build_sensors(registry: SensorRegistry, app=None) -> None:
    global _failures, _query
    _failures = registry.counter(INDEX_FAILURES)
    _query = registry.histogram(QUERY_DURATION)


def count_index_failure() -> None:
    if _failures is not None:
        _failures.inc()


async def timed_query[T](awaitable: Awaitable[T]) -> T:
    """Awaits the search, timing it only while the sensor is on."""
    if _query is None or not _query.is_active():
        return await awaitable
    started = time.monotonic()
    try:
        return await awaitable
    finally:
        _query.observe(time.monotonic() - started)
