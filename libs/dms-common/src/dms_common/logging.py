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

    global _service_name, _bootstrap_level
    # A fresh configuration starts from the bootstrap level again; give back
    # what earlier central settings changed, so the next poll reapplies them.
    for name in list(_applied):
        if name:
            logging.getLogger(name).setLevel(_original_levels.pop(name, logging.NOTSET))
    _applied.clear()
    _service_name = settings.service_name
    _bootstrap_level = logging.getLevelName(settings.log_level.upper())
    _register_log_levels_listener()


# --- Central log levels (P97-S1, ADR 0267) -----------------------------------
#
# monitoring-service stores log levels (global, per service, per logger) and
# hands them out with the sensor configuration every service polls every 15 s.
# Each replica polls on its own, so every replica of a service applies the
# same settings. `DMS_LOG_LEVEL` stays the start value and the fallback for
# the root level; while monitoring-service is unreachable the last applied
# levels stay.

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

_service_name: str | None = None
_bootstrap_level: int = logging.INFO
# Level a logger had before a central setting first touched it (e.g. httpx's
# WARNING default), restored when the setting is removed again.
_original_levels: dict[str, int] = {}
_applied: dict[str, str] = {}


def resolve_log_levels(config: dict, service_name: str) -> tuple[str | None, dict[str, str]]:
    """The root level and the per-logger levels that apply to one service.

    `config` is monitoring-service's distributed form (expired entries are
    already left out): `{"global": level|None, "services": {name: level},
    "loggers": [{"service": name|"*", "logger": name, "level": level}]}`.
    Root: the service's setting, then the global one (`None` = keep the
    bootstrap level). Loggers: the service's own entry wins over `*`."""
    services = config.get("services") or {}
    root = services.get(service_name) or config.get("global")
    loggers: dict[str, str] = {}
    own: dict[str, str] = {}
    for entry in config.get("loggers") or []:
        name, level = entry.get("logger"), entry.get("level")
        if not name or level not in LEVELS:
            continue
        if entry.get("service") == service_name:
            own[name] = level
        elif entry.get("service") == "*":
            loggers[name] = level
    loggers.update(own)
    return (root if root in LEVELS else None), loggers


def _announce(message: str, **extra) -> None:
    """Writes one line whatever the current level is (a switch to ERROR must
    still be visible): the record goes straight to the handlers."""
    root = logging.getLogger()
    record = root.makeRecord(__name__, logging.WARNING, __file__, 0, message, (), None)
    for key, value in extra.items():
        setattr(record, key, value)
    root.handle(record)


def apply_log_levels(config: dict) -> None:
    """Applies the central levels to this process; idempotent (called on
    every poll), announces only actual changes."""
    if _service_name is None:
        return
    root_level, logger_levels = resolve_log_levels(config, _service_name)
    wanted = dict(logger_levels)
    wanted[""] = root_level or logging.getLevelName(_bootstrap_level)
    if wanted == _applied:
        return

    for name in set(_applied) - set(wanted):
        target = logging.getLogger(name)
        target.setLevel(_original_levels.pop(name, logging.NOTSET))
    for name, level in wanted.items():
        target = logging.getLogger(name or None)
        if name and name not in _original_levels:
            _original_levels[name] = target.level
        target.setLevel(level)

    previous = dict(_applied)
    _applied.clear()
    _applied.update(wanted)
    if not previous and wanted == {"": logging.getLevelName(_bootstrap_level)}:
        # First poll with nothing set centrally: nothing changed, no line.
        return
    _announce(
        "log_levels_applied",
        root_level=wanted[""],
        logger_levels={k: v for k, v in wanted.items() if k},
        previous=previous or None,
    )


def _register_log_levels_listener() -> None:
    try:
        from dms_metrics_client.config_client import register_log_levels_listener
    except ImportError:
        return
    register_log_levels_listener(apply_log_levels)
