"""Tracing setup (P94-S1, ADR 0250). The global TracerProvider can be set
only once per process, so this module configures it once with an in-memory
exporter and every test reads and clears that exporter."""

import httpx
import pytest
from dms_common import otel
from dms_common.otel import configure_tracing, set_sampling_ratio, setup_tracing
from dms_common.settings import BaseServiceSettings
from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags


class ExampleSettings(BaseServiceSettings):
    service_name: str = "example-service"


EXPORTER = InMemorySpanExporter()
TRACER = configure_tracing(ExampleSettings(_env_file=None), EXPORTER)


@pytest.fixture(autouse=True)
def _clean():
    EXPORTER.clear()
    set_sampling_ratio(1.0)
    yield
    set_sampling_ratio(1.0)


def test_configure_tracing_records_spans():
    with TRACER.start_as_current_span("do-thing", kind=trace.SpanKind.SERVER):
        pass

    [span] = EXPORTER.get_finished_spans()
    assert span.name == "do-thing"
    assert span.resource.attributes["service.name"] == "example-service"
    assert span.resource.attributes["dms.installation_id"] == "local-dev"


def test_sampling_ratio_applies_to_new_traces_only():
    set_sampling_ratio(0.0)
    assert otel.current_sampling_ratio() == 0.0
    with TRACER.start_as_current_span("dropped", kind=trace.SpanKind.SERVER):
        pass
    assert EXPORTER.get_finished_spans() == ()

    # A request that arrives already sampled is followed regardless - one
    # trace is recorded across every hop or not at all.
    parent = SpanContext(
        trace_id=0x1234, span_id=0x99, is_remote=True, trace_flags=TraceFlags(TraceFlags.SAMPLED)
    )
    with TRACER.start_as_current_span(
        "continued", context=trace.set_span_in_context(NonRecordingSpan(parent))
    ):
        pass
    [span] = EXPORTER.get_finished_spans()
    assert span.name == "continued"
    assert span.context.trace_id == 0x1234

    set_sampling_ratio(1.0)
    with TRACER.start_as_current_span("kept", kind=trace.SpanKind.SERVER):
        pass
    assert [s.name for s in EXPORTER.get_finished_spans()] == ["continued", "kept"]


def test_traces_start_only_at_entry_points():
    """A poll loop's database query or HTTP call is no trace of its own;
    inside a request it is a child span as usual."""
    for kind in (trace.SpanKind.CLIENT, trace.SpanKind.INTERNAL):
        with TRACER.start_as_current_span("poll", kind=kind):
            pass
    assert EXPORTER.get_finished_spans() == ()

    with TRACER.start_as_current_span("request", kind=trace.SpanKind.SERVER):
        with TRACER.start_as_current_span("SELECT", kind=trace.SpanKind.CLIENT):
            pass
    assert [s.name for s in EXPORTER.get_finished_spans()] == ["SELECT", "request"]
    for kind in (trace.SpanKind.CONSUMER, trace.SpanKind.PRODUCER):
        with TRACER.start_as_current_span("event", kind=kind):
            pass
    assert len(EXPORTER.get_finished_spans()) == 4


def test_ratio_is_clamped():
    set_sampling_ratio(7)
    assert otel.current_sampling_ratio() == 1.0
    set_sampling_ratio(-1)
    assert otel.current_sampling_ratio() == 0.0


def test_setup_tracing_is_off_without_an_endpoint():
    assert setup_tracing(FastAPI(), ExampleSettings(_env_file=None)) is False


async def test_trace_crosses_an_http_hop():
    """Service A calls service B over httpx: one trace, B's server span is a
    child of A's client span (the `traceparent` header)."""
    service_b = FastAPI()

    @service_b.get("/inner")
    async def inner(request: Request):
        return {"traceparent": request.headers.get("traceparent")}

    service_a = FastAPI()

    @service_a.get("/outer")
    async def outer():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=service_b), base_url="http://b"
        ) as client:
            HTTPXClientInstrumentor.instrument_client(client)
            return (await client.get("/inner")).json()

    for app in (service_a, service_b):
        FastAPIInstrumentor.instrument_app(app, exclude_spans=["receive", "send"])
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=service_a), base_url="http://a"
    ) as caller:
        body = (await caller.get("/outer")).json()

    spans = EXPORTER.get_finished_spans()
    assert body["traceparent"] is not None
    assert len({s.context.trace_id for s in spans}) == 1
    # In one process B's span is INTERNAL (A's span is still current);
    # across processes it is a SERVER span - the parent link is the same.
    assert sorted(s.name for s in spans) == ["GET", "GET /inner", "GET /outer"]
    inner_span = next(s for s in spans if s.name == "GET /inner")
    client_span = next(s for s in spans if s.kind == trace.SpanKind.CLIENT)
    assert inner_span.parent.span_id == client_span.context.span_id


def test_access_line_names_the_requests_trace(capsys):
    """P94-S2 (ADR 0251): `setup_observability`'s order - access log inside
    the tracing middleware - puts the request's trace id on its access line."""
    import json

    from dms_common.access_log import AccessLogMiddleware
    from dms_common.logging import configure_logging
    from fastapi.testclient import TestClient

    configure_logging(ExampleSettings(_env_file=None))
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.get("/ping")
    async def ping():
        return {}

    FastAPIInstrumentor.instrument_app(app, exclude_spans=["receive", "send"])
    with TestClient(app) as client:
        client.get("/ping")

    [server] = [s for s in EXPORTER.get_finished_spans() if s.name == "GET /ping"]
    lines = [json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
    [access] = [x for x in lines if x["logger"] == "dms.access"]
    assert access["trace_id"] == format(server.context.trace_id, "032x")
