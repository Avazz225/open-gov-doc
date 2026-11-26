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


# --- P88-S2c: cost levels 1-5 and default-off sensors -------------------------


def test_cost_levels_and_legacy_values():
    from dms_metrics_client import SensorSpec, normalize_cost, resolve_active

    assert SensorSpec("a", "g", "cheap", "x").cost == 2
    assert SensorSpec("a", "g", "expensive", "x").cost == 4
    assert SensorSpec("a", "g", 5, "x").as_dict()["cost"] == 5
    assert normalize_cost("3") == 3
    for bad in (0, 6, "medium"):
        try:
            normalize_cost(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)

    overrides = {"cost:4": False}
    assert resolve_active(True, overrides, "s", cost=4) == (False, "cost")
    assert resolve_active(True, overrides, "s", cost=5) == (True, "global")
    # A legacy key still applies to its levels until it is migrated.
    assert resolve_active(True, {"cost:cheap": False}, "s", cost=3) == (False, "cost")
    assert resolve_active(True, {"cost:cheap": False}, "s", cost=4) == (True, "global")


def test_default_off_sensor_needs_an_explicit_override():
    from dms_metrics_client import resolve_active

    assert resolve_active(True, {}, "s", cost=5, default_enabled=False) == (False, "default")
    assert resolve_active(True, {"cost:5": True}, "s", cost=5, default_enabled=False) == (
        True,
        "cost",
    )
    assert resolve_active(True, {"group:g": True}, "s", group="g", default_enabled=False) == (
        True,
        "group",
    )
    assert resolve_active(False, {"s": True}, "s", default_enabled=False) == (True, "sensor")


def test_unbound_proxy_keeps_default_off_sensors_off():
    from dms_metrics_client import SensorConfigProxy, SensorRegistry, SensorSpec

    proxy = SensorConfigProxy()
    registry = SensorRegistry("t", is_active=proxy.is_active)
    normal = registry.counter(SensorSpec("t.normal", "g", 1, "x"))
    breakdown = registry.counter(SensorSpec("t.breakdown", "g", 5, "x", default_enabled=False))
    assert normal.is_active() is True
    assert breakdown.is_active() is False


async def test_tracing_ratio_reaches_registered_listeners():
    """P94-S1 (ADR 0250): the sampling ratio comes with the sensor config."""
    from dms_metrics_client import config_client

    received: list[float] = []
    config_client.register_tracing_ratio_listener(received.append)
    try:
        client = SensorConfigClient(
            "http://monitoring-service",
            client=_client_with_response(
                {"global_default": True, "overrides": {}, "tracing_sampling_ratio": 0.25}
            ),
        )
        await client.start()
        await client.stop()
        assert received == [0.25]
    finally:
        config_client._tracing_ratio_listeners.remove(received.append)


async def test_log_levels_reach_registered_listeners_and_absence_changes_nothing():
    """P97-S1 (ADR 0267): log levels come with the sensor config; an older
    monitoring-service without the key must not reset anything."""
    from dms_metrics_client import config_client

    received: list[dict] = []
    config_client.register_log_levels_listener(received.append)
    levels = {"global": "WARNING", "services": {}, "loggers": []}
    try:
        for body in (
            {"global_default": True, "overrides": {}, "log_levels": levels},
            {"global_default": True, "overrides": {}},
        ):
            client = SensorConfigClient(
                "http://monitoring-service", client=_client_with_response(body)
            )
            await client.start()
            await client.stop()
        assert received == [levels]
    finally:
        config_client._log_levels_listeners.remove(received.append)
