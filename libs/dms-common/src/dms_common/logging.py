import json
import logging
import sys
from datetime import UTC, datetime

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
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(settings: BaseServiceSettings) -> None:
    """Setzt Root-Logger auf strukturiertes JSON-Logging nach stdout.

    Jeder Log-Eintrag enthält automatisch ``service_name``/``environment``,
    damit Log-Aggregation (10.1) Einträge ohne Zusatzparsing zuordnen kann.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())

    old_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.service_name = settings.service_name
        record.environment = settings.environment
        return record

    logging.setLogRecordFactory(factory)
