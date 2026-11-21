from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from dms_metrics_client.sensors import SensorRegistry


def metrics_payload(registry: SensorRegistry) -> tuple[bytes, str]:
    """Serializes the active sensors of a `SensorRegistry` in Prometheus
    exposition format. Deliberately returns raw bytes + content type instead
    of a ready-made `fastapi.Response`, so this lib doesn't need a FastAPI
    dependency - each service builds the response itself
    (`Response(content=body, media_type=content_type)`).

    Runs the registry's synchronous scrape samplers first (P88-S2b: the
    runtime sensors every service gets), so a plain sync `/metrics`
    endpoint still reports them."""
    registry.run_sync_samplers()
    return generate_latest(registry.collector_registry), CONTENT_TYPE_LATEST


async def refreshed_metrics_payload(registry: SensorRegistry) -> tuple[bytes, str]:
    """Like `metrics_payload`, after running the async scrape samplers too
    (P88-S2b: database-backed backlog gauges) - for an async `/metrics`."""
    await registry.run_async_samplers()
    return metrics_payload(registry)
