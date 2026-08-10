import json

import httpx
from dms_cli import credentials
from dms_cli.main import app


def test_config_show_without_login_fails(cli_home, runner):
    result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 1


def test_config_show_prints_gateway_and_username(cli_home, runner, logged_in):
    result = runner.invoke(app, ["config", "show"])

    assert result.exit_code == 0, result.output
    assert "gateway.test" in result.output
    assert "alice" in result.output


def test_config_set_gateway_url_creates_credentials_if_missing(cli_home, runner):
    result = runner.invoke(app, ["config", "set-gateway-url", "http://new.test"])

    assert result.exit_code == 0, result.output
    creds = credentials.load_credentials()
    assert creds.gateway_url == "http://new.test"


def test_config_set_gateway_url_preserves_existing_tokens(cli_home, runner, logged_in):
    result = runner.invoke(app, ["config", "set-gateway-url", "http://new.test"])

    assert result.exit_code == 0, result.output
    creds = credentials.load_credentials()
    assert creds.gateway_url == "http://new.test"
    assert creds.access_token == "access-token"


def test_config_export_prints_json_output(
    cli_home, runner, logged_in, mock_transport_factory, route_json
):
    mock_transport_factory(
        route_json(
            {
                "GET /api/config-service/config/export": {
                    "schema_version": "1.0",
                    "exported_at": "2026-01-01T00:00:00Z",
                    "roles": [{"name": "r1", "description": "d", "permissions": ["read"]}],
                }
            }
        )
    )

    result = runner.invoke(app, ["-o", "json", "config", "export"])

    assert result.exit_code == 0, result.output
    assert '"r1"' in result.output


def test_config_export_writes_to_file(
    cli_home, runner, logged_in, mock_transport_factory, route_json, tmp_path
):
    mock_transport_factory(
        route_json(
            {
                "GET /api/config-service/config/export": {
                    "schema_version": "1.0",
                    "exported_at": "2026-01-01T00:00:00Z",
                }
            }
        )
    )
    out_file = tmp_path / "export.json"

    result = runner.invoke(app, ["config", "export", "--file", str(out_file)])

    assert result.exit_code == 0, result.output
    assert json.loads(out_file.read_text(encoding="utf-8"))["schema_version"] == "1.0"


def test_config_export_with_category_passes_query_param(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["params"] = request.url.params.get_list("categories")
        return httpx.Response(
            200, json={"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"}
        )

    mock_transport_factory(handler)

    result = runner.invoke(app, ["config", "export", "--category", "roles"])

    assert result.exit_code == 0, result.output
    assert captured["params"] == ["roles"]


def test_config_compare_reads_files_and_prints_summary_table(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    base_file = tmp_path / "base.json"
    compare_file = tmp_path / "compare.json"
    base_file.write_text(
        json.dumps({"schema_version": "1.0", "exported_at": "2026-01-01T00:00:00Z"}),
        encoding="utf-8",
    )
    compare_file.write_text(
        json.dumps({"schema_version": "1.0", "exported_at": "2026-01-02T00:00:00Z"}),
        encoding="utf-8",
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "base_exported_at": "2026-01-01T00:00:00Z",
                "compare_exported_at": "2026-01-02T00:00:00Z",
                "categories": {
                    "roles": {
                        "only_in_base": [],
                        "only_in_compare": ["r2"],
                        "differing": {},
                        "identical": ["r1"],
                    }
                },
            },
        )

    mock_transport_factory(handler)

    result = runner.invoke(app, ["config", "compare", str(compare_file), "--base", str(base_file)])

    assert result.exit_code == 0, result.output
    assert "roles" in result.output
    assert captured["body"]["base"]["exported_at"] == "2026-01-01T00:00:00Z"
    assert captured["body"]["compare"]["exported_at"] == "2026-01-02T00:00:00Z"


def test_config_compare_without_base_omits_it_from_request_body(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    compare_file = tmp_path / "compare.json"
    compare_file.write_text(
        json.dumps({"schema_version": "1.0", "exported_at": "2026-01-02T00:00:00Z"}),
        encoding="utf-8",
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "base_exported_at": "2026-01-01T00:00:00Z",
                "compare_exported_at": "2026-01-02T00:00:00Z",
                "categories": {},
            },
        )

    mock_transport_factory(handler)

    result = runner.invoke(app, ["config", "compare", str(compare_file)])

    assert result.exit_code == 0, result.output
    assert "base" not in captured["body"]


def test_config_compare_passes_ignore_regex(
    cli_home, runner, logged_in, mock_transport_factory, tmp_path
):
    compare_file = tmp_path / "compare.json"
    compare_file.write_text(
        json.dumps({"schema_version": "1.0", "exported_at": "2026-01-02T00:00:00Z"}),
        encoding="utf-8",
    )
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "schema_version": "1.0",
                "base_exported_at": "2026-01-01T00:00:00Z",
                "compare_exported_at": "2026-01-02T00:00:00Z",
                "categories": {},
            },
        )

    mock_transport_factory(handler)

    result = runner.invoke(
        app,
        ["config", "compare", str(compare_file), "--ignore-regex", '{"*": "^\\\\d+_+"}'],
    )

    assert result.exit_code == 0, result.output
    assert captured["body"]["ignore_regex"] == {"*": "^\\d+_+"}


def test_config_compare_json_output_includes_full_detail(
    cli_home, runner, logged_in, mock_transport_factory, route_json
):
    mock_transport_factory(
        route_json(
            {
                "POST /api/config-service/config/compare": {
                    "schema_version": "1.0",
                    "base_exported_at": "2026-01-01T00:00:00Z",
                    "compare_exported_at": "2026-01-02T00:00:00Z",
                    "categories": {
                        "roles": {
                            "only_in_base": [],
                            "only_in_compare": [],
                            "differing": {"r1": {"permissions": {"base": [], "compare": ["x"]}}},
                            "identical": [],
                        }
                    },
                }
            }
        )
    )
    compare_file = cli_home / "compare.json"
    compare_file.write_text(
        json.dumps({"schema_version": "1.0", "exported_at": "2026-01-02T00:00:00Z"}),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["-o", "json", "config", "compare", str(compare_file)])

    assert result.exit_code == 0, result.output
    assert '"permissions"' in result.output
