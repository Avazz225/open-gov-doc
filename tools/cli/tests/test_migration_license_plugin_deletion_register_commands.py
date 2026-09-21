import json

import httpx
from dms_cli.main import app


def test_migration_installations_list(
    cli_home, runner, logged_in, mock_transport_factory, route_json
):
    mock_transport_factory(
        route_json(
            {
                "GET /api/migration-service/paired-installations": [
                    {
                        "id": "p1",
                        "display_name": "Zweigstelle Nord",
                        "base_url": "https://nord.example.com",
                        "created_at": "2026-01-01T00:00:00",
                    }
                ]
            }
        )
    )

    result = runner.invoke(app, ["migration", "installations", "list"])

    assert result.exit_code == 0, result.output
    assert "Zweigstelle Nord" in result.output


def test_migration_installations_pair_sends_body_and_warns_about_the_key(
    cli_home, runner, logged_in, mock_transport_factory
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/migration-service/paired-installations"
        body = json.loads(request.content)
        assert body == {
            "display_name": "Zweigstelle Nord",
            "base_url": "https://nord.example.com",
            "api_key": None,
        }
        return httpx.Response(
            200,
            json={
                "id": "p1",
                "display_name": "Zweigstelle Nord",
                "base_url": "https://nord.example.com",
                "created_at": "2026-01-01T00:00:00",
                "api_key": "plaintext-key-shown-once",
            },
        )

    mock_transport_factory(handler)

    result = runner.invoke(
        app,
        [
            "migration",
            "installations",
            "pair",
            "--display-name",
            "Zweigstelle Nord",
            "--base-url",
            "https://nord.example.com",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "plaintext-key-shown-once" in result.output


def test_migration_installations_unpair(cli_home, runner, logged_in, mock_transport_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "DELETE"
        assert request.url.path == "/api/migration-service/paired-installations/p1"
        return httpx.Response(200, json={})

    mock_transport_factory(handler)

    result = runner.invoke(app, ["migration", "installations", "unpair", "p1"])

    assert result.exit_code == 0, result.output


def test_migration_transfers_list_with_status_filter(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    mock_transport_factory(handler)

    result = runner.invoke(app, ["migration", "transfers", "list", "--status", "running"])

    assert result.exit_code == 0, result.output
    assert captured["query"]["status"] == "running"


def test_migration_transfers_create_reports_pending_approval(
    cli_home, runner, logged_in, mock_transport_factory
):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/migration-service/transfers"
        body = json.loads(request.content)
        assert body == {
            "source_folder_id": "f1",
            "target_installation_id": "p1",
            "dry_run": False,
            "retention_days": None,
        }
        return httpx.Response(
            200, json={"status": "pending_approval", "transfer": None, "approval_request_id": "a1"}
        )

    mock_transport_factory(handler)

    result = runner.invoke(
        app,
        [
            "migration",
            "transfers",
            "create",
            "--source-folder-id",
            "f1",
            "--target-installation-id",
            "p1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "a1" in result.output


def test_license_status(cli_home, runner, logged_in, mock_transport_factory, route_json):
    mock_transport_factory(
        route_json(
            {
                "GET /api/license-service/license/status": {
                    "installed": True,
                    "valid": True,
                    "invalid_reason": None,
                    "issued_at": "2026-01-01T00:00:00",
                    "expires_at": "2027-01-01T00:00:00",
                    "days_remaining": 300,
                    "user_model": "named",
                    "users": None,
                    "storage_gb": None,
                    "documents": None,
                    "licensed_components": [],
                    "limits_exceeded": [],
                }
            }
        )
    )

    result = runner.invoke(app, ["license", "status"])

    assert result.exit_code == 0, result.output
    assert "installed" in result.output


def test_license_upload(cli_home, runner, logged_in, mock_transport_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/license-service/license"
        body = json.loads(request.content)
        assert body == {"license_token": "signed-token"}
        return httpx.Response(200, json={"installed": True, "valid": True})

    mock_transport_factory(handler)

    result = runner.invoke(app, ["license", "upload", "--token", "signed-token"])

    assert result.exit_code == 0, result.output


def test_plugin_orchestration_status_combines_plugins_and_nodes(
    cli_home, runner, logged_in, mock_transport_factory, route_json
):
    mock_transport_factory(
        route_json(
            {
                "GET /api/plugin-orchestration-service/plugins": [
                    {
                        "plugin_type": "ocr-tesseract",
                        "version": "1.0",
                        "scaling_type": "stateless_horizontal",
                        "resource_cpu_cores": 2.0,
                        "resource_ram_mb": 512,
                        "load_profile": None,
                        "dependencies": [],
                        "registered_at": "2026-01-01T00:00:00",
                        "updated_at": "2026-01-01T00:00:00",
                    }
                ],
                "GET /api/plugin-orchestration-service/nodes": [
                    {
                        "node_id": "node-1",
                        "cpu_cores": 8,
                        "total_ram_mb": 16384,
                        "cpu_usage_percent": 12.5,
                        "available_ram_mb": 8192,
                        "sampled_at": "2026-01-01T00:00:00",
                    }
                ],
            }
        )
    )

    result = runner.invoke(app, ["plugin-orchestration", "status"])

    assert result.exit_code == 0, result.output
    assert "ocr-tesseract" in result.output
    assert "node-1" in result.output


def test_plugin_orchestration_placements_list_with_filter(
    cli_home, runner, logged_in, mock_transport_factory
):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["query"] = dict(request.url.params)
        return httpx.Response(200, json=[])

    mock_transport_factory(handler)

    result = runner.invoke(
        app, ["plugin-orchestration", "placements", "list", "--plugin-type", "ocr-tesseract"]
    )

    assert result.exit_code == 0, result.output
    assert captured["query"]["plugin_type"] == "ocr-tesseract"


def test_deletion_register_list_document(cli_home, runner, logged_in, mock_transport_factory):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=[
                {
                    "id": "e1",
                    "document_id": "d1",
                    "trigger": "trash_expiry",
                    "reason": None,
                    "triggered_by": None,
                    "occurred_at": "2026-01-01T00:00:00",
                }
            ],
        )

    mock_transport_factory(handler)

    result = runner.invoke(app, ["deletion-register", "list", "--id", "d1"])

    assert result.exit_code == 0, result.output
    assert captured["path"] == "/api/document-service/deletion-register"
    assert captured["query"]["document_id"] == "d1"
    assert "trash_expiry" in result.output


def test_deletion_register_list_folder(cli_home, runner, logged_in, mock_transport_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/folder-service/deletion-register"
        assert dict(request.url.params)["folder_id"] == "f1"
        return httpx.Response(200, json=[])

    mock_transport_factory(handler)

    result = runner.invoke(app, ["deletion-register", "list", "--kind", "folder", "--id", "f1"])

    assert result.exit_code == 0, result.output


def test_deletion_register_reconcile(cli_home, runner, logged_in, mock_transport_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/document-service/documents/d1/reconcile-restore-deletion"
        body = json.loads(request.content)
        assert body == {"original_entry_id": "e1", "reason": "manuell wiederhergestellt"}
        return httpx.Response(204)

    mock_transport_factory(handler)

    result = runner.invoke(
        app,
        [
            "deletion-register",
            "reconcile",
            "d1",
            "--original-entry-id",
            "e1",
            "--reason",
            "manuell wiederhergestellt",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "wiederhergestellt vermerkt" in result.output


def test_deletion_register_invalid_kind_is_rejected(cli_home, runner, logged_in):
    result = runner.invoke(app, ["deletion-register", "list", "--kind", "case"])

    assert result.exit_code == 2
