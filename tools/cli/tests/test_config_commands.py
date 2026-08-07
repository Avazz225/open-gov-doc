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
