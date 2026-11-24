import json
import logging
import sys
from datetime import UTC, datetime

from opentelemetry import trace

from dms_common.settings import BaseServiceSettings

_RESERVED = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Trace correlation (P94-S2, ADR 0251): a line written inside a traced
        # request names its trace, so Grafana can jump from the log to Tempo
        # and back. Unsampled ids are left out - they lead nowhere.
        context = trace.get_current_span().get_span_context()
        if context.is_valid and context.trace_flags.sampled:
            payload["trace_id"] = format(context.trace_id, "032x")
            payload["span_id"] = format(context.span_id, "016x")
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(settings: BaseServiceSettings) -> None:
    """Configures the root logger for structured JSON logging to stdout.

    Every log entry automatically includes ``service_name``/``environment``,
    so log aggregation (10.1) can attribute entries without extra parsing.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    # uvicorn configures its own plain-text handlers before it imports the
    # app (P94-S2): route them through the JSON handler too. Its access log is
    # replaced by `dms_common.access_log`, which writes inside the request's
    # trace; uvicorn's own line comes after the response, without one.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    old_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.service_name = settings.service_name
        record.environment = settings.environment
        return record

    logging.setLogRecordFactory(factory)
