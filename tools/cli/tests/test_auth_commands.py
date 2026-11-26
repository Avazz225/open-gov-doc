import json

import httpx
from dms_cli import client as client_module
from dms_cli import credentials
from dms_cli.main import app


def _fake_login_post(status_code=200):
    def fake_post(url, json, timeout):  # noqa: A002
        if status_code == 200:
            body = {
                "access_token": "a",
                "refresh_token": "r",
                "expires_in": 300,
                "token_type": "bearer",
            }
        else:
            body = {"detail": "invalid credentials"}
        return httpx.Response(status_code, json=body, request=httpx.Request("POST", url))

    return fake_post


def test_login_stores_credentials(cli_home, runner, monkeypatch):
    monkeypatch.setattr(client_module.httpx, "post", _fake_login_post())

    result = runner.invoke(
        app,
        ["login", "--username", "alice", "--password", "secret", "--gateway-url", "http://gw.test"],
    )

    assert result.exit_code == 0, result.output
    creds = credentials.load_credentials()
    assert creds.access_token == "a"
    assert creds.refresh_token == "r"
    assert creds.username == "alice"
    assert creds.gateway_url == "http://gw.test"


def test_login_with_bad_credentials_fails(cli_home, runner, monkeypatch):
    monkeypatch.setattr(client_module.httpx, "post", _fake_login_post(status_code=401))

    result = runner.invoke(app, ["login", "--username", "alice", "--password", "wrong"])

    assert result.exit_code != 0
    assert credentials.load_credentials() is None


def test_login_warns_when_dms_token_env_var_set(cli_home, runner, monkeypatch):
    monkeypatch.setattr(client_module.httpx, "post", _fake_login_post())
    monkeypatch.setenv("DMS_TOKEN", "env-token")

    result = runner.invoke(app, ["login", "--username", "alice", "--password", "secret"])

    assert "DMS_TOKEN" in result.output


def test_logout_clears_credentials(cli_home, runner, logged_in):
    result = runner.invoke(app, ["logout"])

    assert result.exit_code == 0
    assert credentials.load_credentials() is None


def test_whoami_without_login_fails(cli_home, runner):
    result = runner.invoke(app, ["whoami"])

    assert result.exit_code == 1
    assert "Nicht angemeldet" in result.output


def test_whoami_prints_current_user(cli_home, runner, logged_in, mock_transport_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/auth-service/me"
        assert request.headers.get("authorization") == "Bearer access-token"
        return httpx.Response(200, json={"id": "u1", "username": "alice", "enabled": True})

    mock_transport_factory(handler)

    result = runner.invoke(app, ["whoami"])

    assert result.exit_code == 0, result.output
    assert "alice" in result.output


def test_whoami_json_output(cli_home, runner, logged_in, mock_transport_factory):
    mock_transport_factory(lambda r: httpx.Response(200, json={"id": "u1", "username": "alice"}))

    result = runner.invoke(app, ["-o", "json", "whoami"])

    assert result.exit_code == 0, result.output
    assert '"username": "alice"' in result.output


def test_whoami_adds_domain_roles_and_superuser_state(
    cli_home, runner, logged_in, mock_transport_factory
):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth-service/me":
            return httpx.Response(200, json={"sub": "u1", "preferred_username": "alice"})
        if path == "/api/permission-service/effective-permissions/u1/root":
            return httpx.Response(
                200, json={"roles": ["domain-admin-users", "domain-admin-config"]}
            )
        if path == "/api/auth-service/superuser/status":
            return httpx.Response(200, json={"active": True, "principal_id": "u1"})
        raise AssertionError(f"unexpected request {path}")

    mock_transport_factory(handler)

    result = runner.invoke(app, ["-o", "json", "whoami"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["domain_roles"] == ["domain-admin-config", "domain-admin-users"]
    assert data["superuser_active"] is True


def test_whoami_superuser_of_someone_else_is_not_active_and_errors_are_skipped(
    cli_home, runner, logged_in, mock_transport_factory
):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/auth-service/me":
            return httpx.Response(200, json={"sub": "u1", "preferred_username": "alice"})
        if path.startswith("/api/permission-service/"):
            return httpx.Response(403, json={"detail": "Fehlende Domain-Admin-Rolle"})
        return httpx.Response(200, json={"active": True, "principal_id": "someone-else"})

    mock_transport_factory(handler)

    result = runner.invoke(app, ["-o", "json", "whoami"])

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "domain_roles" not in data
    assert data["superuser_active"] is False


def test_invalid_output_format_rejected(cli_home, runner):
    result = runner.invoke(app, ["-o", "xml", "whoami"])

    assert result.exit_code == 2
