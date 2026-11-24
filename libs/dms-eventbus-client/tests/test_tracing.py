"""Trace propagation through NATS headers (P94-S1, ADR 0250): the consumer's
span continues the publisher's trace. Real NATS, in-memory span exporter."""

import asyncio
import os
import uuid

from dms_eventbus_client import NatsEventBusClient
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

NATS_URL = os.environ.get("TEST_NATS_URL", "nats://localhost:4222")

EXPORTER = InMemorySpanExporter()
_provider = TracerProvider()
_provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
trace.set_tracer_provider(_provider)


async def test_consumer_continues_the_publishers_trace():
    stream = f"test_{uuid.uuid4().hex[:8]}"
    bus = NatsEventBusClient(NATS_URL, stream)
    await bus.connect()
    seen: list[int] = []
    done = asyncio.Event()

    async def handler(payload: bytes) -> None:
        seen.append(trace.get_current_span().get_span_context().trace_id)
        done.set()

    try:
        await bus.subscribe(f"{stream}.created", handler, durable=None)
        await asyncio.sleep(0.2)
        tracer = trace.get_tracer("test")
        with tracer.start_as_current_span("request", kind=trace.SpanKind.SERVER) as request_span:
            await bus.publish(f"{stream}.created", b"{}")
        await asyncio.wait_for(done.wait(), timeout=5)
    finally:
        await bus.close()

    trace_id = request_span.get_span_context().trace_id
    assert seen == [trace_id]
    spans = {s.name: s for s in EXPORTER.get_finished_spans()}
    publish = spans[f"{stream}.created publish"]
    process = spans[f"{stream}.created process"]
    assert publish.kind == trace.SpanKind.PRODUCER
    assert process.kind == trace.SpanKind.CONSUMER
    assert process.parent.span_id == publish.context.span_id
    assert process.attributes["messaging.system"] == "nats"
