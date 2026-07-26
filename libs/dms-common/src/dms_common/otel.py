from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter

from dms_common.settings import BaseServiceSettings


def configure_tracing(settings: BaseServiceSettings, exporter: SpanExporter) -> trace.Tracer:
    """Setzt einen globalen TracerProvider und liefert einen Tracer zurück.

    Der konkrete Exporter (Console für lokale Entwicklung, OTLP sobald
    Phase 11/Monitoring den Collector bereitstellt) wird bewusst vom Aufrufer
    übergeben statt hier hart verdrahtet - hält diese Lib frei von einer
    festen Abhängigkeit auf einen bestimmten Collector.
    """
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: settings.service_name}))
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return trace.get_tracer(settings.service_name)
