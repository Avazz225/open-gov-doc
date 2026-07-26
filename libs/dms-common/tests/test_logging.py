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
