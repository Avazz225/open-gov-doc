from dms_metrics_client import SensorRegistry
from federation_hub_service import metrics


def _always_active_registry() -> SensorRegistry:
    return SensorRegistry("federation-hub-service-test", is_active=lambda name: True)


def test_sensor_declarations_includes_both_retry_cache_sensors_and_http():
    names = {declaration["name"] for declaration in metrics.sensor_declarations()}
    assert "federation_hub.retry_cache.forward_pending" in names
    assert "federation_hub.retry_cache.result_pending" in names
    assert "http.requests" in names


async def test_build_samplers_reports_the_live_cache_sizes():
    """Both caches are plain in-process dicts - the sampler functions must
    read `len(...)` directly, not a stale snapshot, so a later change to
    the dict is reflected on the NEXT tick without re-registering
    anything (see the second test below)."""
    registry = _always_active_registry()
    forward_gauge, result_gauge = metrics.build_sensor_registry(registry)
    pending_payloads = {"a": {}, "b": {}}
    pending_result_payloads = {"c": {}}
    samplers = metrics.build_samplers(
        forward_gauge, result_gauge, pending_payloads, pending_result_payloads
    )

    forward_value = await samplers[metrics.FORWARD_RETRY_CACHE_DEPTH.name][1]()
    result_value = await samplers[metrics.RESULT_RETRY_CACHE_DEPTH.name][1]()

    assert forward_value == 2.0
    assert result_value == 1.0


async def test_build_samplers_reflects_cache_changes_live():
    registry = _always_active_registry()
    forward_gauge, result_gauge = metrics.build_sensor_registry(registry)
    pending_payloads: dict = {}
    samplers = metrics.build_samplers(forward_gauge, result_gauge, pending_payloads, {})
    compute_forward = samplers[metrics.FORWARD_RETRY_CACHE_DEPTH.name][1]

    assert await compute_forward() == 0.0

    pending_payloads["handover-1"] = {"encrypted_payload": "opaque"}
    assert await compute_forward() == 1.0

    pending_payloads.pop("handover-1")
    assert await compute_forward() == 0.0
