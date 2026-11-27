"""The CLI commands added for the admin skill (P96-S2, ADR 0253 §5)."""

import json

import httpx
from dms_cli.main import app


class Recorder:
    """A MockTransport handler that answers by "METHOD path" and records
    every request (method, path, query, body)."""

    def __init__(self, routes: dict):
        self.routes = routes
        self.calls: list[tuple[str, str, dict, object]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        body = None
        if request.content and request.headers.get("content-type", "").startswith(
            "application/json"
        ):
            body = json.loads(request.content)
        elif request.content:
            body = request.content
        self.calls.append((request.method, request.url.path, dict(request.url.params), body))
        status, payload = self.routes.get(key, (404, {"detail": f"no route {key}"}))
        return (
            httpx.Response(status, json=payload) if payload is not None else httpx.Response(status)
        )


def _invoke(runner, args):
    return runner.invoke(app, args)


def test_config_import_reports_the_counts(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    package = tmp_path / "pkg.json"
    package.write_text(json.dumps({"schema_version": "1", "roles": []}))
    recorder = Recorder(
        {
            "POST /api/config-service/config/import": (
                200,
                {
                    "status": "applied",
                    "result": {
                        "schema_version": "1",
                        "results": {
                            "roles": {"created": 2, "updated": 1, "skipped": 4, "errors": []}
                        },
                    },
                },
            )
        }
    )
    mock_transport_factory(recorder)

    result = _invoke(runner, ["config", "import", str(package), "--category", "roles"])

    assert result.exit_code == 0, result.output
    assert "roles" in result.output and "2" in result.output
    [(_, _, params, body)] = recorder.calls
    assert params == {"categories": "roles"}
    assert body == {"schema_version": "1", "roles": []}


def test_config_import_under_four_eyes_says_nothing_changed(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    package = tmp_path / "pkg.json"
    package.write_text("{}")
    mock_transport_factory(
        Recorder(
            {
                "POST /api/config-service/config/import": (
                    200,
                    {"status": "pending_approval", "approval_request_id": "req-7"},
                )
            }
        )
    )
    result = _invoke(runner, ["config", "import", str(package)])
    assert result.exit_code == 0, result.output
    assert "req-7" in result.output and "nichts geaendert" in result.output


def test_config_import_fails_on_category_errors(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    package = tmp_path / "pkg.json"
    package.write_text("{}")
    mock_transport_factory(
        Recorder(
            {
                "POST /api/config-service/config/import": (
                    200,
                    {
                        "status": "applied",
                        "result": {
                            "schema_version": "1",
                            "results": {"alert_rules": {"errors": ["sensor unknown"]}},
                        },
                    },
                )
            }
        )
    )
    result = _invoke(runner, ["config", "import", str(package)])
    assert result.exit_code == 1
    assert "sensor unknown" in result.output


def test_permission_explain_renders_the_walk(cli_home, runner, logged_in, mock_transport_factory):
    recorder = Recorder(
        {
            "GET /api/permission-service/explain/frau-x/akte-1": (
                200,
                {
                    "allowed": False,
                    "reason": (
                        "Keine passende Zuweisung; die Vererbung endet an einem Knoten "
                        "mit inherit=false."
                    ),
                    "groups": ["Bauamt"],
                    "steps": [
                        {
                            "resource_id": "akte-1",
                            "resource_type": "folder",
                            "registered": True,
                            "inherit": False,
                            "assignments": [],
                            "active_locks": [],
                        }
                    ],
                    "cached": None,
                },
            )
        }
    )
    mock_transport_factory(recorder)

    result = _invoke(runner, ["permission", "explain", "frau-x", "akte-1"])

    assert result.exit_code == 0, result.output
    assert "VERWEIGERT" in result.output and "inherit=false" in result.output
    assert "Aufstieg endet" in result.output
    assert recorder.calls[0][2] == {"permission": "folder.read", "access_type": "read"}


def test_group_add_resolves_the_name(cli_home, runner, logged_in, mock_transport_factory):
    recorder = Recorder(
        {
            "GET /api/permission-service/groups": (200, [{"id": "g1", "name": "Poststelle"}]),
            "POST /api/permission-service/groups/g1/members": (201, {"principal_id": "kaya"}),
        }
    )
    mock_transport_factory(recorder)
    result = _invoke(runner, ["group", "add", "Poststelle", "kaya"])
    assert result.exit_code == 0, result.output
    assert recorder.calls[-1][3] == {"principal_id": "kaya"}


def test_monitoring_alerts_shows_only_firing_rules(
    cli_home, runner, logged_in, mock_transport_factory
):
    mock_transport_factory(
        Recorder(
            {
                "GET /api/monitoring-service/alert-rules": (
                    200,
                    [
                        {"id": "1", "name": "Speicher voll", "sensor": "s", "level": "error"},
                        {"id": "2", "name": "Ruhig", "sensor": "s", "level": "ok"},
                    ],
                )
            }
        )
    )
    result = _invoke(runner, ["monitoring", "alerts"])
    assert result.exit_code == 0, result.output
    assert "Speicher voll" in result.output and "Ruhig" not in result.output


def test_alert_rule_preview_sends_the_draft(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    draft = tmp_path / "rule.json"
    draft.write_text(json.dumps({"name": "x", "sensor": "storage.used_ratio", "max_warning": 0.8}))
    recorder = Recorder(
        {
            "POST /api/monitoring-service/alert-rules/preview": (
                200,
                {"value": 0.42, "level": "ok", "series": []},
            )
        }
    )
    mock_transport_factory(recorder)
    result = _invoke(runner, ["monitoring", "alert-rule", "preview", str(draft)])
    assert result.exit_code == 0, result.output
    assert recorder.calls[0][3]["max_warning"] == 0.8
    assert "0.42" in result.output


def test_approvals_list_and_reject(cli_home, runner, logged_in, mock_transport_factory):
    recorder = Recorder(
        {
            "GET /api/permission-service/approval-requests": (
                200,
                [{"id": "r1", "action_type": "config.import", "status": "pending"}],
            ),
            "POST /api/permission-service/approval-requests/r1/reject": (200, {"id": "r1"}),
        }
    )
    mock_transport_factory(recorder)
    listed = _invoke(runner, ["approvals", "list", "--action-type", "config.import"])
    assert listed.exit_code == 0, listed.output
    assert recorder.calls[0][2] == {"status": "pending", "action_type": "config.import"}
    rejected = _invoke(runner, ["approvals", "reject", "r1", "--reason", "falsches Paket"])
    assert rejected.exit_code == 0, rejected.output
    assert recorder.calls[-1][3] == {"reason": "falsches Paket"}


def test_retention_set_writes_both_owners_and_keeps_other_values(
    cli_home, runner, logged_in, mock_transport_factory
):
    current = {
        "deletion_reason_required": True,
        "reminder_lead_days": 7,
        "deletion_reason_catalog": ["Dublette"],
    }
    routes = {}
    for owner in ("document-service", "folder-service"):
        routes[f"GET /api/{owner}/retention-config"] = (200, current)
        routes[f"GET /api/{owner}/trash-config"] = (200, {"restore_period_days": 60})
        routes[f"PUT /api/{owner}/retention-config"] = (200, current)
        routes[f"PUT /api/{owner}/trash-config"] = (200, {"restore_period_days": 60})
    recorder = Recorder(routes)
    mock_transport_factory(recorder)

    result = _invoke(
        runner, ["retention", "set", "--trash-days", "60", "--add-reason", "Fehlablage"]
    )

    assert result.exit_code == 0, result.output
    puts = [(path, body) for method, path, _, body in recorder.calls if method == "PUT"]
    assert {path for path, _ in puts} == {
        "/api/document-service/trash-config",
        "/api/folder-service/trash-config",
        "/api/document-service/retention-config",
        "/api/folder-service/retention-config",
    }
    retention = [body for path, body in puts if path.endswith("retention-config")]
    assert all(
        body
        == {
            "deletion_reason_required": True,
            "reminder_lead_days": 7,
            "deletion_reason_catalog": ["Dublette", "Fehlablage"],
        }
        for body in retention
    )


def test_failures_list_collects_every_kind(cli_home, runner, logged_in, mock_transport_factory):
    mock_transport_factory(
        Recorder(
            {
                "GET /api/rendering-service/renditions": (
                    200,
                    [
                        {
                            "id": "rd1",
                            "status": "failed",
                            "document_id": "d1",
                            "error_message": "soffice",
                        }
                    ],
                ),
                "GET /api/ocr-service/ocr-results": (200, []),
                "GET /api/notification-service/notifications": (
                    200,
                    [{"id": "n1", "status": "failed", "recipient": "alice"}],
                ),
            }
        )
    )
    result = _invoke(runner, ["-o", "json", "failures", "list"])
    assert result.exit_code == 0, result.output
    kinds = {row["kind"] for row in json.loads(result.output)}
    assert kinds == {"rendition", "notification"}


def _failures_routes(now):
    from datetime import timedelta

    def at(hours):
        return (now - timedelta(hours=hours)).isoformat()

    return {
        "GET /api/rendering-service/renditions": (
            200,
            [
                {
                    "id": "old",
                    "status": "failed_permanent",
                    "document_id": "d1",
                    "error_message": "Stream has ended",
                    "attempts": 5,
                    "updated_at": at(72),
                },
                {
                    "id": "new",
                    "status": "failed_permanent",
                    "document_id": "d2",
                    "error_message": "Stream has ended",
                    "attempts": 5,
                    "updated_at": at(1),
                },
            ],
        ),
        "GET /api/ocr-service/ocr-results": (
            200,
            [
                {
                    "id": "o1",
                    "status": "failed",
                    "document_id": "d3",
                    "error_message": "PDF nicht lesbar",
                    "attempts": 1,
                    "updated_at": at(5),
                }
            ],
        ),
        "GET /api/notification-service/notifications": (200, []),
    }


def test_failures_list_is_newest_first_with_time_and_filters(
    cli_home, runner, logged_in, mock_transport_factory
):
    """P98-S1: the time of the last attempt, newest first, --since/--status/
    --limit, and --group counting by cause."""
    from datetime import UTC, datetime

    recorder = Recorder(_failures_routes(datetime.now(UTC)))
    mock_transport_factory(recorder)

    rows = json.loads(_invoke(runner, ["-o", "json", "failures", "list"]).output)
    # Each status is asked separately, so the mock answers twice per kind.
    ids = [row["id"] for row in rows]
    assert ids[0] == "new" and ids[-1] == "old"
    assert all(row["when"] for row in rows)

    recent = json.loads(
        _invoke(runner, ["-o", "json", "failures", "list", "--since", "24h"]).output
    )
    assert "old" not in {row["id"] for row in recent}

    before = len(recorder.calls)
    permanent = json.loads(
        _invoke(
            runner,
            ["-o", "json", "failures", "list", "--status", "failed_permanent", "--limit", "1"],
        ).output
    )
    assert [row["id"] for row in permanent] == ["new"]
    # Notifications have no permanent state, so only renditions and OCR are asked.
    statuses = {call[2].get("status") for call in recorder.calls[before:]}
    assert statuses == {"failed_permanent"}

    grouped = json.loads(
        _invoke(runner, ["-o", "json", "failures", "list", "--kind", "rendition", "--group"]).output
    )
    top = grouped[0]
    assert top["kind"] == "rendition" and top["error"] == "Stream has ended"
    assert top["count"] >= 2


def test_failures_since_rejects_nonsense(cli_home, runner, logged_in, mock_transport_factory):
    mock_transport_factory(Recorder({}))
    result = _invoke(runner, ["failures", "list", "--since", "gestern"])
    assert result.exit_code == 2


def test_workflow_upload_is_multipart_and_terminate_needs_a_reason(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    bpmn = tmp_path / "p.bpmn"
    bpmn.write_text("<bpmn/>")
    recorder = Recorder(
        {
            "POST /api/workflow-service/process-definitions": (201, {"id": 5, "version": 2}),
            "POST /api/workflow-service/instances/i1/terminate": (200, {"operation": "terminate"}),
        }
    )
    mock_transport_factory(recorder)

    uploaded = _invoke(runner, ["workflow", "definitions", "upload", str(bpmn), "--name", "Antrag"])
    assert uploaded.exit_code == 0, uploaded.output
    assert b'name="bpmn_xml"' in recorder.calls[0][3]

    missing = _invoke(runner, ["workflow", "instances", "terminate", "i1"])
    assert missing.exit_code != 0
    terminated = _invoke(
        runner, ["workflow", "instances", "terminate", "i1", "--reason", "Testrest"]
    )
    assert terminated.exit_code == 0, terminated.output
    assert recorder.calls[-1][3] == {"reason": "Testrest", "expected_version": None}
