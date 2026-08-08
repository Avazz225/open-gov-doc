from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from dms_metrics_client.sensors import SensorRegistry


def metrics_payload(registry: SensorRegistry) -> tuple[bytes, str]:
    """Serialisiert die aktiven Sensoren einer `SensorRegistry` im
    Prometheus-Exposition-Format. Liefert bewusst rohe Bytes + Content-Type
    statt einer fertigen `fastapi.Response`, damit diese Lib kein
    FastAPI-Abhängigkeit braucht - jeder Service baut die Response selbst
    (`Response(content=body, media_type=content_type)`)."""
    return generate_latest(registry.collector_registry), CONTENT_TYPE_LATEST
