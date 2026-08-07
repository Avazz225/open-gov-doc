import stat

from dms_cli import credentials


def test_save_and_load_roundtrip(cli_home):
    creds = credentials.Credentials(
        gateway_url="http://gw.test", access_token="a", refresh_token="r", username="bob"
    )
    credentials.save_credentials(creds)

    loaded = credentials.load_credentials()

    assert loaded == creds


def test_credentials_file_has_owner_only_permissions(cli_home):
    creds = credentials.Credentials(gateway_url="http://gw.test", access_token="a")
    credentials.save_credentials(creds)

    mode = stat.S_IMODE(credentials.credentials_path().stat().st_mode)

    assert mode == stat.S_IRUSR | stat.S_IWUSR


def test_load_credentials_without_file_returns_none(cli_home):
    assert credentials.load_credentials() is None


def test_clear_credentials_removes_file(cli_home):
    credentials.save_credentials(credentials.Credentials(gateway_url="http://gw.test"))

    credentials.clear_credentials()

    assert not credentials.credentials_path().exists()
    assert credentials.load_credentials() is None


def test_clear_credentials_without_file_is_noop(cli_home):
    credentials.clear_credentials()  # keine Datei vorhanden - darf nicht scheitern


def test_env_token_overrides_stored_file(cli_home, monkeypatch):
    credentials.save_credentials(
        credentials.Credentials(gateway_url="http://file.test", access_token="file-token")
    )
    monkeypatch.setenv("DMS_TOKEN", "env-token")
    monkeypatch.setenv("DMS_GATEWAY_URL", "http://env.test")

    loaded = credentials.load_credentials()

    assert loaded.access_token == "env-token"
    assert loaded.gateway_url == "http://env.test"
    assert loaded.refresh_token is None


def test_env_token_without_gateway_url_falls_back_to_default(cli_home, monkeypatch):
    monkeypatch.setenv("DMS_TOKEN", "env-token")

    loaded = credentials.load_credentials()

    assert loaded.gateway_url == credentials.DEFAULT_GATEWAY_URL


def test_gateway_url_env_override_applies_to_file_based_credentials(cli_home, monkeypatch):
    credentials.save_credentials(
        credentials.Credentials(gateway_url="http://file.test", access_token="file-token")
    )
    monkeypatch.setenv("DMS_GATEWAY_URL", "http://override.test")

    loaded = credentials.load_credentials()

    assert loaded.gateway_url == "http://override.test"
    assert loaded.access_token == "file-token"
