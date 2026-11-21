import httpx
from dms_metrics_client import SensorConfigClient


def _client_with_response(payload: dict) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return httpx.AsyncClient(
        base_url="http://monitoring-service", transport=httpx.MockTransport(handler)
    )


async def test_is_active_reflects_override_over_global_default():
    client = SensorConfigClient(
        "http://monitoring-service",
        client=_client_with_response({"global_default": True, "overrides": {"some.sensor": False}}),
    )
    await client.start()
    try:
        assert client.is_active("some.sensor") is False
        assert client.is_active("other.sensor") is True
    finally:
        await client.stop()


async def test_fails_open_to_true_before_first_successful_fetch():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = SensorConfigClient(
        "http://monitoring-service",
        client=httpx.AsyncClient(
            base_url="http://monitoring-service", transport=httpx.MockTransport(handler)
        ),
    )
    assert client.is_active("anything") is True
    await client.start()
    try:
        assert client.is_active("anything") is True
    finally:
        await client.stop()


async def test_fails_open_to_last_known_state_on_later_error():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(200, json={"global_default": False, "overrides": {"x": True}})
        return httpx.Response(500)

    client = SensorConfigClient(
        "http://monitoring-service",
        client=httpx.AsyncClient(
            base_url="http://monitoring-service", transport=httpx.MockTransport(handler)
        ),
    )
    await client.start()
    try:
        assert client.is_active("x") is True
        assert client.is_active("y") is False
        await client._refresh_once()  # simuliert den nächsten, fehlschlagenden Poll-Tick
        assert client.is_active("x") is True
        assert client.is_active("y") is False
    finally:
        await client.stop()


# --- P88-S2 (ADR 0243): group and cost-class overrides ------------------------


def test_most_specific_setting_wins():
    from dms_metrics_client import resolve_active

    overrides = {"cost:expensive": False, "group:storage": True, "storage.big": False}
    # Sensor override beats everything.
    assert resolve_active(True, overrides, "storage.big", group="storage", cost="expensive") == (
        False,
        "sensor",
    )
    # Group beats cost: an expensive storage sensor is on through its group.
    assert resolve_active(True, overrides, "storage.x", group="storage", cost="expensive") == (
        True,
        "group",
    )
    assert resolve_active(True, overrides, "doc.x", group="performance", cost="expensive") == (
        False,
        "cost",
    )
    assert resolve_active(False, overrides, "doc.y", group="performance", cost="cheap") == (
        False,
        "global",
    )


async def test_registry_sensors_follow_group_and_cost_overrides():
    from dms_metrics_client import SensorConfigProxy, SensorRegistry, SensorSpec

    client = SensorConfigClient(
        "http://monitoring-service",
        client=_client_with_response(
            {"global_default": True, "overrides": {"cost:expensive": False}}
        ),
    )
    proxy = SensorConfigProxy()
    registry = SensorRegistry("test-service", is_active=proxy.is_active)
    cheap = registry.counter(SensorSpec("t.cheap", "performance", "cheap", "x"))
    expensive = registry.counter(SensorSpec("t.expensive", "performance", "expensive", "x"))
    await client.start()
    proxy.bind(client)
    try:
        assert cheap.is_active() is True
        assert expensive.is_active() is False
    finally:
        proxy.unbind()
        await client.stop()
