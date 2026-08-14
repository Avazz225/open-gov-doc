from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from dms_metrics_client.sensors import SensorRegistry


def metrics_payload(registry: SensorRegistry) -> tuple[bytes, str]:
    """Serializes the active sensors of a `SensorRegistry` in Prometheus
    exposition format. Deliberately returns raw bytes + content type instead
    of a ready-made `fastapi.Response`, so this lib doesn't need a FastAPI
    dependency - each service builds the response itself
    (`Response(content=body, media_type=content_type)`)."""
    return generate_latest(registry.collector_registry), CONTENT_TYPE_LATEST
