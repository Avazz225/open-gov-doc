import httpx
from dms_cli.main import app


def test_events_list_prints_table_and_filter_summary(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "events": [{"id": "e1", "event_type": "document.created"}],
                "total_before_filter": 5,
                "total_after_filter": 1,
                "superuser": False,
            },
        )

    mock_transport_factory(handler)

    result = runner.invoke(app, ["query", "events", "list", "--actor", "u1", "--limit", "10"])

    assert result.exit_code == 0, result.output
    assert captured["query"] == {"actor": "u1", "limit": "10"}
    assert "document.created" in result.output
    assert "1/5 Ereignisse" in result.output


def test_events_list_json_output(cli_home, runner, logged_in, mock_transport_factory):
    mock_transport_factory(
        lambda r: httpx.Response(
            200,
            json={
                "events": [],
                "total_before_filter": 0,
                "total_after_filter": 0,
                "superuser": True,
            },
        )
    )

    result = runner.invoke(app, ["-o", "json", "query", "events", "list"])

    assert result.exit_code == 0, result.output
    assert '"superuser": true' in result.output


def test_query_text_posts_query_text(cli_home, runner, logged_in, mock_transport_factory):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = httpx.Request("POST", request.url, content=request.content).content
        return httpx.Response(
            200,
            json={
                "events": [],
                "total_before_filter": 0,
                "total_after_filter": 0,
                "superuser": False,
            },
        )

    mock_transport_factory(handler)

    result = runner.invoke(app, ["query", "text", "SELECT * FROM events"])

    assert result.exit_code == 0, result.output
    assert b"SELECT * FROM events" in captured["body"]


def test_manipulation_mode_status(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(
        route_json(
            {
                "GET /api/query-service/manipulation-mode/status": {
                    "active": False,
                    "activated_by": None,
                    "expires_at": None,
                }
            }
        )
    )

    result = runner.invoke(app, ["query", "manipulation-mode", "status"])

    assert result.exit_code == 0, result.output
    assert "False" in result.output


def test_manipulation_mode_activate_sends_duration(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(
            200, json={"active": True, "activated_by": "alice", "expires_at": "2026-01-01T00:00:00"}
        )

    mock_transport_factory(handler)

    result = runner.invoke(app, ["query", "manipulation-mode", "activate", "--minutes", "30"])

    assert result.exit_code == 0, result.output
    assert b'"duration_minutes":30' in captured["body"]


def test_manipulate_dry_run_parses_params_as_json_and_strings(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as jsonlib

        captured["body"] = jsonlib.loads(request.content)
        return httpx.Response(
            200,
            json={
                "action_type": "document.attribute_reset",
                "preview": "1 Dokument betroffen",
                "is_critical": False,
                "dry_run_token": "tok",
            },
        )

    mock_transport_factory(handler)

    result = runner.invoke(
        app,
        [
            "query",
            "manipulate",
            "dry-run",
            "--action",
            "document.attribute_reset",
            "--param",
            "document_id=42",
            "--param",
            "attribute_key=Kennzeichen",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["body"]["action_type"] == "document.attribute_reset"
    # "42" wird als JSON-Zahl geparst, "Kennzeichen" bleibt String (kein gueltiges JSON).
    assert captured["body"]["params"] == {"document_id": 42, "attribute_key": "Kennzeichen"}
    assert "tok" in result.output


def test_manipulate_dry_run_rejects_param_without_equals(cli_home, runner, logged_in):
    result = runner.invoke(
        app, ["query", "manipulate", "dry-run", "--action", "x", "--param", "no-equals-sign"]
    )

    assert result.exit_code == 2


def test_manipulate_execute_sends_dry_run_token(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"status": "executed", "result": {"ok": True}})

    mock_transport_factory(handler)

    result = runner.invoke(app, ["query", "manipulate", "execute", "--dry-run-token", "tok-123"])

    assert result.exit_code == 0, result.output
    assert b"tok-123" in captured["body"]
    assert "executed" in result.output


def test_approvals_list_filters_to_known_manipulation_action_types(
    cli_home, runner, logged_in, mock_transport_factory
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"status": "pending"}
        return httpx.Response(
            200,
            json=[
                {
                    "id": "r1",
                    "action_type": "document.attribute_reset",
                    "initiated_by": "alice",
                    "payload": {},
                    "status": "pending",
                    "approved_by": None,
                    "rejected_by": None,
                    "reason": None,
                    "created_at": "2026-01-01T00:00:00",
                    "decided_at": None,
                },
                {
                    "id": "r2",
                    "action_type": "auth.superuser.activate",
                    "initiated_by": "bob",
                    "payload": {},
                    "status": "pending",
                    "approved_by": None,
                    "rejected_by": None,
                    "reason": None,
                    "created_at": "2026-01-01T00:00:00",
                    "decided_at": None,
                },
            ],
        )

    mock_transport_factory(handler)

    result = runner.invoke(app, ["-o", "json", "query", "approvals", "list"])

    assert result.exit_code == 0, result.output
    assert "r1" in result.output
    assert "r2" not in result.output


def test_approvals_approve_defaults_approved_by_to_stored_username(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["body"] = request.content
        return httpx.Response(200, json={"id": "r1", "status": "approved"})

    mock_transport_factory(handler)

    result = runner.invoke(app, ["query", "approvals", "approve", "r1"])

    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/permission-service/approval-requests/r1/approve"
    assert b'"approved_by":"alice"' in captured["body"]


def test_approvals_approve_with_explicit_approved_by(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.content
        return httpx.Response(200, json={"id": "r1", "status": "approved"})

    mock_transport_factory(handler)

    result = runner.invoke(app, ["query", "approvals", "approve", "r1", "--approved-by", "bob"])

    assert result.exit_code == 0, result.output
    assert b'"approved_by":"bob"' in captured["body"]
