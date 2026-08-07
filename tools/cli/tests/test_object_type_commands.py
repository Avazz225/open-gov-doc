import json

import httpx
from dms_cli.main import app


def test_list(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(
        route_json(
            {
                "GET /api/object-type-service/object-types": [
                    {"id": 1, "name": "Rechnung", "applies_to": "document"}
                ]
            }
        )
    )

    result = runner.invoke(app, ["object-type", "list"])

    assert result.exit_code == 0, result.output
    assert "Rechnung" in result.output


def test_get(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(
        route_json({"GET /api/object-type-service/object-types/1": {"id": 1, "name": "Rechnung"}})
    )

    result = runner.invoke(app, ["object-type", "get", "1"])

    assert result.exit_code == 0, result.output
    assert "Rechnung" in result.output


def test_create_reads_json_file_as_body(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    payload = {"name": "Vertrag", "applies_to": "document"}
    file_path = tmp_path / "object-type.json"
    file_path.write_text(json.dumps(payload), encoding="utf-8")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(201, json={"id": 2, **payload})

    mock_transport_factory(handler)

    result = runner.invoke(app, ["object-type", "create", "--file", str(file_path)])

    assert result.exit_code == 0, result.output
    assert captured["body"] == payload


def test_update_sends_put(cli_home, runner, logged_in, mock_transport_factory, tmp_path):
    file_path = tmp_path / "update.json"
    file_path.write_text(json.dumps({"attributes": []}), encoding="utf-8")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        return httpx.Response(200, json={"id": 1, "attributes": []})

    mock_transport_factory(handler)

    result = runner.invoke(app, ["object-type", "update", "1", "--file", str(file_path)])

    assert result.exit_code == 0, result.output
    assert captured["method"] == "PUT"


def test_delete(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(
        route_json({"DELETE /api/object-type-service/object-types/1": (204, None)})
    )

    result = runner.invoke(app, ["object-type", "delete", "1"])

    assert result.exit_code == 0, result.output
    assert "geloescht" in result.output


def test_create_with_missing_file_fails(cli_home, runner, logged_in):
    result = runner.invoke(app, ["object-type", "create", "--file", "/nonexistent.json"])

    assert result.exit_code != 0
