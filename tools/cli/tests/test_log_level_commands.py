"""`dms logging …` (P97-S2, ADR 0267)."""

import json

import httpx
import pytest
import typer
from dms_cli.commands.log_levels import parse_duration
from dms_cli.main import app


class Recorder:
    """Answers by "METHOD path" and records (method, path, query, json body)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str, dict, object]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, dict(request.url.params), body))
        status, payload = self.routes.get(key, (404, {"detail": f"no route {key}"}))
        return httpx.Response(status, json=payload)


VIEW = {
    "settings": [
        {
            "scope": "logger:workflow-service:workflow_service.repository",
            "service": "workflow-service",
            "logger": "workflow_service.repository",
            "level": "DEBUG",
            "expires_at": "2099-01-01T00:00:00Z",
            "updated_by": "alice",
            "updated_at": "2026-11-26T19:00:00Z",
        }
    ],
    "effective": {
        "workflow-service": {
            "root_level": None,
            "root_source": "env",
            "loggers": {"workflow_service.repository": {"level": "DEBUG", "source": "service"}},
        },
        "permission-service": {"root_level": "WARNING", "root_source": "global", "loggers": {}},
    },
    "catalog": {
        "workflow-service": ["sqlalchemy", "workflow_service.main", "workflow_service.repository"]
    },
    "debug_default_minutes": 60,
    "debug_max_minutes": 1440,
}
PATH = "/api/monitoring-service/log-levels"


def test_set_sends_scope_level_and_duration(cli_home, runner, logged_in, mock_transport_factory):
    recorder = Recorder({f"PUT {PATH}": (200, VIEW)})
    mock_transport_factory(recorder)

    result = runner.invoke(
        app,
        [
            "logging",
            "set",
            "debug",
            "--service",
            "workflow-service",
            "--logger",
            "workflow_service.repository",
            "--for",
            "1h30m",
        ],
    )

    assert result.exit_code == 0, result.output
    [(method, _path, _query, body)] = recorder.calls
    assert method == "PUT"
    assert body == {
        "level": "debug",
        "service": "workflow-service",
        "logger": "workflow_service.repository",
        "expires_in_minutes": 90,
    }
    assert "logger:workflow-service:workflow_service.repository = DEBUG" in result.output
    assert "15 s" in result.output


def test_set_without_service_is_global_and_refusals_pass_through(
    cli_home, runner, logged_in, mock_transport_factory
):
    recorder = Recorder({f"PUT {PATH}": (403, {"detail": "Fehlende Domain-Admin-Rolle 'X'"})})
    mock_transport_factory(recorder)
    result = runner.invoke(app, ["logging", "set", "WARNING"])
    assert result.exit_code != 0
    # `run()` prints it for the user; CliRunner sees the exception itself.
    assert "Fehlende Domain-Admin-Rolle" in str(result.exception)
    assert recorder.calls[0][3] == {"level": "WARNING"}


def test_reset_sends_the_scope_as_query(cli_home, runner, logged_in, mock_transport_factory):
    recorder = Recorder({f"DELETE {PATH}": (200, VIEW)})
    mock_transport_factory(recorder)
    result = runner.invoke(
        app, ["logging", "reset", "--service", "workflow-service", "--logger", "httpx"]
    )
    assert result.exit_code == 0, result.output
    assert recorder.calls[0][2] == {"service": "workflow-service", "logger": "httpx"}


def test_show_lists_every_service_and_one_in_detail(
    cli_home, runner, logged_in, mock_transport_factory
):
    mock_transport_factory(Recorder({f"GET {PATH}": (200, VIEW)}))

    overview = runner.invoke(app, ["logging", "show"])
    assert overview.exit_code == 0, overview.output
    assert "permission-service" in overview.output and "global" in overview.output
    assert "workflow_service.repository=DEBUG" in overview.output

    detail = runner.invoke(app, ["-o", "json", "logging", "show", "--service", "workflow-service"])
    assert json.loads(detail.output)["root_source"] == "env"

    unknown = runner.invoke(app, ["logging", "show", "--service", "nope"])
    assert unknown.exit_code == 1


def test_loggers_filters_the_catalog(cli_home, runner, logged_in, mock_transport_factory):
    mock_transport_factory(Recorder({f"GET {PATH}": (200, VIEW)}))
    result = runner.invoke(
        app, ["logging", "loggers", "--service", "workflow-service", "--filter", "workflow"]
    )
    assert result.exit_code == 0
    assert result.output.split() == ["workflow_service.main", "workflow_service.repository"]


@pytest.mark.parametrize(("text", "minutes"), [("30m", 30), ("2h", 120), ("1h30m", 90), ("45", 45)])
def test_durations(text, minutes):
    assert parse_duration(text) == minutes


@pytest.mark.parametrize("text", ["", "0", "abc", "1d"])
def test_bad_durations(text):
    with pytest.raises(typer.BadParameter):
        parse_duration(text)
