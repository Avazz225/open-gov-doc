import json
import logging

from dms_common.logging import configure_logging
from dms_common.settings import BaseServiceSettings


class ExampleSettings(BaseServiceSettings):
    service_name: str = "example-service"


def test_configure_logging_emits_json(capsys):
    settings = ExampleSettings(_env_file=None)
    configure_logging(settings)

    logging.getLogger("test").info("hello", extra={"document_id": "doc-1"})

    captured = capsys.readouterr()
    line = json.loads(captured.out.strip().splitlines()[-1])

    assert line["message"] == "hello"
    assert line["level"] == "INFO"
    assert line["service_name"] == "example-service"
    assert line["environment"] == "development"
    assert line["document_id"] == "doc-1"


def _last_json_line(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_log_line_carries_the_sampled_trace(capsys):
    """P94-S2 (ADR 0251): a line written inside a sampled span names its
    trace; an unsampled one doesn't."""
    from opentelemetry import trace
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags

    configure_logging(ExampleSettings(_env_file=None))
    sampled = SpanContext(
        trace_id=0xABC, span_id=0x12, is_remote=False, trace_flags=TraceFlags(TraceFlags.SAMPLED)
    )
    with trace.use_span(NonRecordingSpan(sampled)):
        logging.getLogger("test").info("inside")
    line = _last_json_line(capsys)
    assert line["trace_id"] == f"{0xABC:032x}"
    assert line["span_id"] == f"{0x12:016x}"

    unsampled = SpanContext(trace_id=0xABC, span_id=0x12, is_remote=False)
    with trace.use_span(NonRecordingSpan(unsampled)):
        logging.getLogger("test").info("not sampled")
    assert "trace_id" not in _last_json_line(capsys)


def test_uvicorn_loggers_go_through_the_json_handler(capsys):
    configure_logging(ExampleSettings(_env_file=None))
    logging.getLogger("uvicorn.error").info("Started server process")
    assert _last_json_line(capsys)["logger"] == "uvicorn.error"
    # uvicorn's own access line is replaced by dms_common.access_log.
    assert logging.getLogger("uvicorn.access").getEffectiveLevel() == logging.WARNING


def test_access_log_writes_one_structured_line_per_request(capsys):
    from dms_common.access_log import AccessLogMiddleware
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    configure_logging(ExampleSettings(_env_file=None))
    app = FastAPI()
    app.add_middleware(AccessLogMiddleware)

    @app.get("/documents/{document_id}")
    async def get_document(document_id: str):
        return {"id": document_id}

    @app.get("/healthz")
    async def healthz():
        return {}

    with TestClient(app) as client:
        client.get("/documents/42")
        client.get("/healthz")
        client.get("/nowhere")

    lines = [json.loads(x) for x in capsys.readouterr().out.strip().splitlines()]
    access = [x for x in lines if x["logger"] == "dms.access"]
    assert [(x["http_route"], x["http_path"], x["http_status"]) for x in access] == [
        ("/documents/{document_id}", "/documents/42", 200),
        (None, "/nowhere", 404),
    ]
    assert access[0]["message"] == "GET /documents/{document_id} 200"
    assert access[0]["duration_ms"] >= 0
