from dms_common.otel import configure_tracing
from dms_common.settings import BaseServiceSettings
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


class ExampleSettings(BaseServiceSettings):
    service_name: str = "example-service"


def test_configure_tracing_records_spans():
    settings = ExampleSettings(_env_file=None)
    exporter = InMemorySpanExporter()

    tracer = configure_tracing(settings, exporter)
    with tracer.start_as_current_span("do-thing"):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "do-thing"
    assert spans[0].resource.attributes["service.name"] == "example-service"
