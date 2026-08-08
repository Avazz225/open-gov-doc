import asyncio

from dms_metrics_client import SensorRegistry, SensorSpec, metrics_payload
from dms_metrics_client.loop import run_gauge_sampler_loop


async def test_sampler_loop_only_computes_active_sensors():
    active = {"on.sensor": True, "off.sensor": False}
    registry = SensorRegistry("test-service", is_active=lambda name: active[name])
    on_gauge = registry.gauge(SensorSpec("on.sensor", "test", "cheap", "an"))
    off_gauge = registry.gauge(SensorSpec("off.sensor", "test", "cheap", "aus"))

    calls = {"on": 0, "off": 0}

    async def compute_on() -> float:
        calls["on"] += 1
        return 7.0

    async def compute_off() -> float:
        calls["off"] += 1
        return 9.0

    task = asyncio.create_task(
        run_gauge_sampler_loop(
            {
                "on.sensor": (on_gauge, compute_on),
                "off.sensor": (off_gauge, compute_off),
            },
            interval_seconds=0.05,
        )
    )
    try:
        await asyncio.sleep(0.12)
    finally:
        task.cancel()

    assert calls["on"] > 0
    assert calls["off"] == 0
    body, _ = metrics_payload(registry)
    assert b"on_sensor 7.0" in body
    assert b"off_sensor" not in body or b"off_sensor 9.0" not in body
