from __future__ import annotations

import time

from fastapi import FastAPI

from dms_metrics_client.config_client import SensorConfigProxy
from dms_metrics_client.sensors import GuardedCounter, GuardedHistogram, SensorRegistry, SensorSpec

# Generic per-request sensors (10.1 full rollout, post-pilot) - the same two
# names/specs are registered by every instrumented service, so
# monitoring-service's merged /metrics carries one uniform pair of metric
# families across all services (disambiguated by the `service` label added
# in `monitoring_service.scraper.merge_metric_families`), rather than N
# differently-named per-service variants. Request count, average/p95/p99
# response time, error count, and successful-request count are all derived
# from these two via PromQL (see docs/operations/monitoring.md) - a separate
# sensor per derived statistic would just be four toggles for data that's
# either all collected together or not at all, since they share one
# underlying histogram.
HTTP_REQUESTS = SensorSpec(
    name="http.requests",
    group="performance",
    cost="cheap",
    description="Anzahl HTTP-Anfragen je Methode/Route/Statuscode",
)
HTTP_REQUEST_DURATION = SensorSpec(
    name="http.request.duration_seconds",
    group="performance",
    cost="cheap",
    description=(
        "Dauer von HTTP-Anfragen in Sekunden je Methode/Route - Basis fuer "
        "Durchschnitt (Sum/Count) sowie p95/p99 (histogram_quantile)"
    ),
)

# Excluded from instrumentation: /healthz is polled far more often than real
# traffic (container health checks) and would dominate the counters with
# noise; /metrics is monitoring-service's own scrape of this instance, same
# reasoning.
_EXCLUDED_PATHS = frozenset({"/healthz", "/metrics"})


def http_sensor_declarations() -> list[dict]:
    return [HTTP_REQUESTS.as_dict(), HTTP_REQUEST_DURATION.as_dict()]


def build_http_sensors(registry: SensorRegistry) -> tuple[GuardedCounter, GuardedHistogram]:
    requests_counter = registry.counter(HTTP_REQUESTS, labelnames=("method", "route", "status"))
    duration_histogram = registry.histogram(HTTP_REQUEST_DURATION, labelnames=("method", "route"))
    return requests_counter, duration_histogram


def install_http_metrics(
    app: FastAPI, requests_counter: GuardedCounter, duration_histogram: GuardedHistogram
) -> None:
    """Registers a generic request-instrumentation middleware. Both sensors
    are checked independently and skip their own work when inactive (10.1:
    "no generation, no buffering" even for the cheap `time.monotonic()` call)
    - a service running with only the counter enabled never pays for timing,
    and vice versa. The route is read from `request.scope["route"]` AFTER
    `call_next()` - Starlette only populates it once routing has actually
    matched an endpoint - falling back to a fixed "unmatched" label for 404s
    so typos/probes against arbitrary paths can't explode the route
    cardinality of the metric."""

    @app.middleware("http")
    async def _http_metrics_middleware(request, call_next):
        if request.url.path in _EXCLUDED_PATHS:
            return await call_next(request)

        counter_active = requests_counter.is_active()
        histogram_active = duration_histogram.is_active()
        if not counter_active and not histogram_active:
            return await call_next(request)

        started_at = time.monotonic() if histogram_active else None
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            route = request.scope.get("route")
            route_path = route.path if route is not None else "unmatched"
            if counter_active:
                requests_counter.inc(
                    method=request.method, route=route_path, status=str(status_code)
                )
            if histogram_active and started_at is not None:
                duration_histogram.observe(
                    time.monotonic() - started_at, method=request.method, route=route_path
                )


def bootstrap_http_sensors(
    app: FastAPI, service_name: str
) -> tuple[SensorConfigProxy, SensorRegistry, GuardedCounter, GuardedHistogram]:
    """One-call setup for the generic per-request sensors (10.1 full
    rollout, identical wiring in every instrumented service). MUST be
    called at **module level**, right after `app = FastAPI(...)` - not from
    inside the async `lifespan` function. `install_http_metrics` registers
    an ASGI middleware, and Starlette freezes the middleware stack once the
    lifespan protocol begins (`app.add_middleware(...)` raises
    `RuntimeError: Cannot add middleware after an application has started`
    if attempted from inside `lifespan`).

    Returns a `SensorConfigProxy`, not a `SensorConfigClient` - the actual
    network-connected client must still be constructed fresh inside
    `lifespan` on every startup (`SensorConfigProxy`'s docstring explains
    why: its `httpx.AsyncClient` can't outlive the event loop it was first
    used on). Also returns the `SensorRegistry` so a service can register
    further, bespoke sensors on the same registry (one registry per
    service, since it owns the single `CollectorRegistry` that `/metrics`
    serializes) - build those with `registry.counter(...)` etc. same as
    `build_http_sensors` does internally."""
    proxy = SensorConfigProxy()
    sensor_registry = SensorRegistry(service_name, is_active=proxy.is_active)
    requests_counter, duration_histogram = build_http_sensors(sensor_registry)
    install_http_metrics(app, requests_counter, duration_histogram)
    return proxy, sensor_registry, requests_counter, duration_histogram
