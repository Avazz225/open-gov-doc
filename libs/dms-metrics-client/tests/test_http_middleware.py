from dms_metrics_client import (
    SensorRegistry,
    bootstrap_http_sensors,
    build_http_sensors,
    install_http_metrics,
    metrics_payload,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _build_app(*, active: bool = True):
    registry = SensorRegistry("test-service", is_active=lambda name: active)
    requests_counter, duration_histogram = build_http_sensors(registry)
    app = FastAPI()
    install_http_metrics(app, requests_counter, duration_histogram)

    @app.get("/documents/{document_id}")
    def get_doc(document_id: str):
        return {"id": document_id}

    @app.get("/boom")
    def boom():
        raise ValueError("kaputt")

    return app, registry


def test_records_request_count_and_duration_by_route_not_raw_path():
    app, registry = _build_app()
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/documents/abc-123")
    client.get("/documents/def-456")

    body, _ = metrics_payload(registry)
    # Both calls hit the same *route template*, not two distinct raw paths -
    # this is the whole point of reading request.scope["route"] instead of
    # request.url.path (cardinality explosion for parameterized paths).
    assert b'route="/documents/{document_id}"' in body
    assert b'method="GET"' in body
    assert b'status="200"' in body
    assert b"http_requests_total" in body
    assert b"http_request_duration_seconds_count" in body
    assert b"http_request_duration_seconds_sum" in body


def test_unmatched_route_falls_back_to_fixed_label():
    app, registry = _build_app()
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/does-not-exist")

    body, _ = metrics_payload(registry)
    assert b'route="unmatched"' in body
    assert b'status="404"' in body


def test_exception_is_recorded_as_500_and_still_propagates():
    app, registry = _build_app()
    client = TestClient(app, raise_server_exceptions=True)
    try:
        client.get("/boom")
    except ValueError:
        pass
    else:
        raise AssertionError("expected the ValueError to propagate")

    body, _ = metrics_payload(registry)
    assert b'route="/boom"' in body
    assert b'status="500"' in body


def test_healthz_and_metrics_are_excluded_from_instrumentation():
    app, registry = _build_app()

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    client = TestClient(app, raise_server_exceptions=False)
    client.get("/healthz")

    body, _ = metrics_payload(registry)
    assert b'route="/healthz"' not in body


def test_no_work_at_all_when_both_sensors_inactive():
    app, registry = _build_app(active=False)
    client = TestClient(app, raise_server_exceptions=False)
    client.get("/documents/abc-123")

    body, _ = metrics_payload(registry)
    # No labeled series at all was ever recorded while deactivated - only the
    # HELP/TYPE header lines are present, no samples.
    assert b'route="/documents/{document_id}"' not in body
    assert b"http_requests_total{" not in body


def test_bootstrap_wires_proxy_registry_and_middleware_together():
    app = FastAPI()
    proxy, registry, requests_counter, duration_histogram = bootstrap_http_sensors(
        app, "bootstrap-test-service"
    )
    assert registry.service_name == "bootstrap-test-service"
    assert proxy.is_active("http.requests") is True  # fail-open, nothing bound yet

    @app.get("/ping")
    def ping():
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    client.get("/ping")

    body, _ = metrics_payload(registry)
    assert b'route="/ping"' in body
    # Sensors returned by bootstrap_http_sensors are the same objects the
    # installed middleware writes to, not disconnected copies.
    assert requests_counter.is_active()
    assert duration_histogram.is_active()


def test_bootstrap_survives_multiple_independent_lifespan_cycles():
    """Reproduces the bug bootstrap_http_sensors's SensorConfigProxy
    indirection exists to fix: an earlier version bound the registry
    directly to one SensorConfigClient's `is_active`, constructed once at
    module import. Each `TestClient(app)` `with` block runs its own event
    loop, and a SensorConfigClient's httpx.AsyncClient can't survive past
    the loop it was first used on - the second cycle's `.start()` failed
    with "Cannot send a request, as the client has been closed." Binding
    a *fresh* SensorConfigClient into the proxy on every lifespan startup
    (as every real service's main.py does) must work for any number of
    independent app lifecycles, exactly as it did before the http.*
    sensors existed at all."""
    from contextlib import asynccontextmanager

    from dms_metrics_client import SensorConfigClient

    app = FastAPI()
    proxy, registry, _requests_counter, _duration_histogram = bootstrap_http_sensors(
        app, "multi-cycle-service"
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = SensorConfigClient("http://monitoring-service.invalid")
        await client.start()  # fails open (no server), still exercises the httpx client
        proxy.bind(client)
        yield
        proxy.unbind()
        await client.stop()

    app.router.lifespan_context = lifespan

    @app.get("/ping")
    def ping():
        return {"ok": True}

    for _ in range(2):
        with TestClient(app, raise_server_exceptions=True) as client:
            response = client.get("/ping")
            assert response.status_code == 200
