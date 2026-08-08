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
