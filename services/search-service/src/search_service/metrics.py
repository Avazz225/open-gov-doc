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

from dms_metrics_client import (
    GuardedCounter,
    GuardedHistogram,
    SensorRegistry,
    SensorSpec,
    throttled,
)

INDEX_FAILURES = SensorSpec(
    name="search.index.failures",
    group="reliability",
    cost=1,
    description=(
        "Fehlgeschlagene Indexierungen (Event wird von NATS erneut zugestellt) seit Prozessstart"
    ),
)
QUERY_DURATION = SensorSpec(
    name="search.query.duration_seconds",
    group="performance",
    cost=4,
    description="Dauer der Volltext-/Attributsuche in der Datenbank in Sekunden",
)

# P88-S2c (package E): an active probe - one full-text search against the
# real index, timed and labelled by result. Level 5, off by default, at
# most once per PROBE_INTERVAL.
PROBE = SensorSpec(
    name="search.probe.duration_seconds",
    group="reliability",
    cost=5,
    description="Test-Suche gegen den Index: Dauer in Sekunden, Label result ok/failed",
    default_enabled=False,
)
PROBE_INTERVAL_SECONDS = 300.0
PROBE_QUERY = "dms monitoring probe"

_failures: GuardedCounter | None = None
_query: GuardedHistogram | None = None


def build_sensors(registry: SensorRegistry, app=None) -> None:
    global _failures, _query
    _failures = registry.counter(INDEX_FAILURES)
    _query = registry.histogram(QUERY_DURATION)

    async def probe() -> dict[tuple[str, ...], float]:
        """Never raises - a failed search is a reported result."""
        from search_service import repository

        started = time.monotonic()
        try:
            async with app.state.session_factory() as session:
                await repository.search(
                    session,
                    query=PROBE_QUERY,
                    folder_id=None,
                    object_type_id=None,
                    created_by=None,
                    created_after=None,
                    created_before=None,
                    attr_filters=[],
                    limit=1,
                    offset=0,
                    sort="relevance",
                )
            result = "ok"
        except Exception:
            result = "failed"
        return {(result,): time.monotonic() - started}

    registry.on_scrape_async(
        registry.gauge(PROBE, labelnames=("result",)),
        throttled(probe, min_interval_seconds=PROBE_INTERVAL_SECONDS),
    )


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
