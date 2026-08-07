import json

import httpx
from dms_cli.main import app


def test_registry_status_all(cli_home, runner, logged_in, mock_transport_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/registry-service/instances"
        return httpx.Response(
            200,
            json=[
                {
                    "instance_id": "i1",
                    "service_type": "document-service",
                    "version": "0.1.0",
                    "capabilities": [],
                    "health_endpoint": "/healthz",
                    "address": "http://document-service:8000",
                    "registered_at": "2026-01-01T00:00:00",
                    "last_heartbeat_at": "2026-01-01T00:00:00",
                    "healthy": True,
                }
            ],
        )

    mock_transport_factory(handler)

    result = runner.invoke(app, ["registry", "status"])

    assert result.exit_code == 0, result.output
    assert "document-service" in result.output


def test_registry_status_for_one_service_type(cli_home, runner, logged_in, mock_transport_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/registry-service/instances/document-service"
        return httpx.Response(200, json=[])

    mock_transport_factory(handler)

    result = runner.invoke(app, ["registry", "status", "document-service"])

    assert result.exit_code == 0, result.output


def test_workflow_definitions_list(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(
        route_json(
            {
                "GET /api/workflow-service/process-definitions": [
                    {
                        "id": 1,
                        "name": "Freigabe",
                        "version": 1,
                        "bpmn_process_id": "p1",
                        "created_at": "2026-01-01T00:00:00",
                        "updated_at": "2026-01-01T00:00:00",
                    }
                ]
            }
        )
    )

    result = runner.invoke(app, ["workflow", "definitions", "list"])

    assert result.exit_code == 0, result.output
    assert "Freigabe" in result.output


def test_workflow_instances_list_with_status_filter(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    mock_transport_factory(handler)

    result = runner.invoke(app, ["workflow", "instances", "list", "--status", "running"])

    assert result.exit_code == 0, result.output
    assert captured["query"]["status"] == "running"


def test_workflow_instances_tasks(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(
        route_json(
            {
                "GET /api/workflow-service/instances/i1/tasks": [
                    {
                        "id": "t1",
                        "name": "Pruefen",
                        "lane": "reviewer",
                        "data": {},
                        "extensions": {},
                    }
                ]
            }
        )
    )

    result = runner.invoke(app, ["workflow", "instances", "tasks", "i1"])

    assert result.exit_code == 0, result.output
    assert "Pruefen" in result.output


def test_workflow_complete_task_without_file_sends_empty_data(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "i1",
                "process_definition_id": 1,
                "business_key": None,
                "status": "running",
                "created_by": "alice",
                "created_at": "2026-01-01T00:00:00",
                "updated_at": "2026-01-01T00:00:00",
                "completed_at": None,
            },
        )

    mock_transport_factory(handler)

    result = runner.invoke(
        app,
        ["workflow", "instances", "complete-task", "i1", "t1", "--completed-by", "alice"],
    )

    assert result.exit_code == 0, result.output
    assert captured["body"] == {"completed_by": "alice", "data": {}, "signature_id": None}


def test_archival_transfers_list(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(
        route_json(
            {
                "GET /api/archival-service/archival-transfers": [
                    {"id": "t1", "document_id": "d1", "status": "released", "created_at": "x"}
                ]
            }
        )
    )

    result = runner.invoke(app, ["archival", "transfers", "list"])

    assert result.exit_code == 0, result.output
    assert "released" in result.output


def test_archival_transfers_retrieve(cli_home, runner, logged_in, mock_transport_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/archival-service/archival-transfers/t1/retrieve"
        return httpx.Response(200, json={"id": "t1", "status": "released"})

    mock_transport_factory(handler)

    result = runner.invoke(app, ["archival", "transfers", "retrieve", "t1"])

    assert result.exit_code == 0, result.output


def test_archival_case_transfers_package_writes_file(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/archival-service/case-archival-transfers/c1/package"
        return httpx.Response(200, content=b"zip-bytes")

    mock_transport_factory(handler)
    out_path = tmp_path / "package.zip"

    result = runner.invoke(
        app, ["archival", "case-transfers", "package", "c1", "--out", str(out_path)]
    )

    assert result.exit_code == 0, result.output
    assert out_path.read_bytes() == b"zip-bytes"
