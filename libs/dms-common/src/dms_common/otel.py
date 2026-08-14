from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter

from dms_common.settings import BaseServiceSettings


def configure_tracing(settings: BaseServiceSettings, exporter: SpanExporter) -> trace.Tracer:
    """Sets a global TracerProvider and returns a tracer.

    The concrete exporter (console for local development, OTLP once
    phase 11/monitoring provides the collector) is deliberately passed in by
    the caller instead of being hardwired here - keeps this lib free of a
    fixed dependency on a specific collector.
    """
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: settings.service_name}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(settings.service_name)
