"""`dms events dead-letters …` (P99-S2)."""

import json

import httpx
from dms_cli.main import app

PATH = "/api/monitoring-service/dead-letters"
LETTER = {
    "seq": 7,
    "stream": "folder",
    "consumer": "permission-service",
    "subject": "folder.resource.moved",
    "stream_seq": "41",
    "deliveries": 12,
    "error": "IntegrityError: parent missing",
    "failed_at": "2026-11-27T12:00:00+00:00",
    "replayed_from": None,
    "size": 80,
}


class Recorder:
    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str, dict, object]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, dict(request.url.params), body))
        status, payload = self.routes.get(key, (404, {"detail": f"no route {key}"}))
        return httpx.Response(status, json=payload)


def test_list_show_replay_discard(cli_home, runner, logged_in, mock_transport_factory):
    recorder = Recorder(
        {
            f"GET {PATH}": (200, [LETTER]),
            f"GET {PATH}/7": (200, {**LETTER, "payload": '{"a": 1}', "payload_truncated": False}),
            f"POST {PATH}/7/replay": (202, LETTER),
            f"DELETE {PATH}/7": (200, LETTER),
        }
    )
    mock_transport_factory(recorder)

    listed = runner.invoke(
        app, ["events", "dead-letters", "list", "--consumer", "permission-service"]
    )
    assert listed.exit_code == 0, listed.output
    assert "folder.resource.moved" in listed.output

    shown = runner.invoke(app, ["events", "dead-letters", "show", "7"])
    assert '"a": 1' in shown.output

    replayed = runner.invoke(
        app, ["events", "dead-letters", "replay", "7", "--reason", "Ordner angelegt"]
    )
    assert replayed.exit_code == 0
    assert "permission-service" in replayed.output

    discarded = runner.invoke(
        app, ["events", "dead-letters", "discard", "7", "--reason", "Test-Altlast"]
    )
    assert discarded.exit_code == 0

    calls = {(m, p): (q, b) for m, p, q, b in recorder.calls}
    assert calls[("POST", f"{PATH}/7/replay")][1] == {"reason": "Ordner angelegt"}
    assert calls[("DELETE", f"{PATH}/7")][0] == {"reason": "Test-Altlast"}


def test_replay_and_discard_require_a_reason(cli_home, runner, logged_in, mock_transport_factory):
    mock_transport_factory(Recorder({}))
    assert runner.invoke(app, ["events", "dead-letters", "replay", "7"]).exit_code == 2
    assert runner.invoke(app, ["events", "dead-letters", "discard", "7"]).exit_code == 2


def test_list_shortens_long_errors_in_the_table_only(
    cli_home, runner, logged_in, mock_transport_factory
):
    long_letter = {**LETTER, "error": "IntegrityError: " + "x" * 300}
    mock_transport_factory(Recorder({f"GET {PATH}": (200, [long_letter])}))
    table = runner.invoke(app, ["events", "dead-letters", "list"])
    assert "x" * 100 not in table.output and "…" in table.output
    raw = runner.invoke(app, ["-o", "json", "events", "dead-letters", "list"])
    assert json.loads(raw.output)[0]["error"] == long_letter["error"]
