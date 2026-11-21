"""P88-S2b: runtime sensors and pull-on-scrape samplers."""

from contextlib import asynccontextmanager

from dms_db_base import build_engine
from dms_metrics_client import (
    SensorRegistry,
    SensorSpec,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
    refreshed_metrics_payload,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _line(body: bytes, metric: str) -> str:
    return next(line for line in body.decode().splitlines() if line.startswith(metric + " "))


def test_every_service_declares_the_runtime_sensors():
    names = {d["name"] for d in http_sensor_declarations()}
    assert {
        "http.requests.in_flight",
        "db.pool.in_use",
        "process.memory_bytes",
        "service.startup.duration_seconds",
    } <= names


def test_bootstrapped_app_reports_runtime_values_and_startup_time():
    @asynccontextmanager
    async def lifespan(app):
        yield

    app = FastAPI(lifespan=lifespan)
    proxy, registry, _requests, _duration = bootstrap_http_sensors(app, "test-service")

    @app.get("/ping")
    def ping():
        return {"ok": True}

    engine = build_engine("postgresql+asyncpg://u:p@localhost:1/none")  # never connects
    with TestClient(app) as client:
        client.get("/ping")
        body, _ = metrics_payload(registry)
    assert float(_line(body, "process_memory_bytes").split()[1]) > 0
    assert float(_line(body, "process_open_fds").split()[1]) > 0
    assert float(_line(body, "service_startup_duration_seconds").split()[1]) >= 0
    # Request finished: nothing in flight any more.
    assert float(_line(body, "http_requests_in_flight").split()[1]) == 0
    assert float(_line(body, "db_pool_in_use").split()[1]) == 0
    assert float(_line(body, "db_pool_size").split()[1]) >= 5
    del engine


def test_inactive_sampler_is_not_called_and_failures_do_not_break_the_scrape():
    calls = []
    active = {"t.on": True, "t.off": False, "t.broken": True}
    registry = SensorRegistry("t", is_active=lambda name: active[name])
    on = registry.gauge(SensorSpec("t.on", "capacity", "cheap", "x"))
    off = registry.gauge(SensorSpec("t.off", "capacity", "cheap", "x"))
    broken = registry.gauge(SensorSpec("t.broken", "capacity", "cheap", "x"))
    registry.on_scrape(on, lambda: calls.append("on") or 3.0)
    registry.on_scrape(off, lambda: calls.append("off") or 4.0)
    registry.on_scrape(broken, lambda: 1 / 0)

    body, _ = metrics_payload(registry)

    assert calls == ["on"]
    assert _line(body, "t_on").endswith("3.0")


async def test_async_samplers_fill_labelled_gauges_and_drop_vanished_labels():
    registry = SensorRegistry("t", is_active=lambda name: True)
    gauge = registry.gauge(
        SensorSpec("t.backlog", "reliability", "cheap", "x"), labelnames=("kind",)
    )
    results = [{("ocr",): 4, ("pdf",): 1}, {("ocr",): 2}]
    registry.on_scrape_async(gauge, lambda: _next(results))

    body, _ = await refreshed_metrics_payload(registry)
    assert b't_backlog{kind="pdf"} 1.0' in body
    body, _ = await refreshed_metrics_payload(registry)
    assert b't_backlog{kind="ocr"} 2.0' in body
    assert b'kind="pdf"' not in body


async def _next(results):
    return results.pop(0)


def test_optional_gauge_is_absent_until_it_has_a_value():
    registry = SensorRegistry("t", is_active=lambda name: True)
    gauge = registry.gauge(SensorSpec("t.days", "capacity", "cheap", "x"), optional=True)
    values = [None, 12.0, None]
    registry.on_scrape(gauge, lambda: values.pop(0))

    def samples() -> list[str]:
        body = metrics_payload(registry)[0].decode()
        return [line for line in body.splitlines() if line.startswith("t_days")]

    assert samples() == []  # never set: absent, not 0
    assert samples() == ["t_days 12.0"]
    assert samples() == []  # sampler reports None again: removed


async def test_throttled_sampler_runs_at_most_once_per_interval():
    from dms_metrics_client import throttled

    calls = []
    clock = {"t": 0.0}

    async def probe():
        calls.append(clock["t"])
        return float(len(calls))

    sample = throttled(probe, min_interval_seconds=300, clock=lambda: clock["t"])
    assert await sample() == 1.0
    clock["t"] = 299
    assert await sample() == 1.0  # cached
    clock["t"] = 300
    assert await sample() == 2.0
    assert calls == [0.0, 300]
