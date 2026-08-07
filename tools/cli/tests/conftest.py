import httpx
import pytest
from dms_cli import credentials
from dms_cli.client import GatewayClient
from typer.testing import CliRunner


@pytest.fixture
def cli_home(tmp_path, monkeypatch):
    """Isoliert die Credential-Datei je Test (statt echtem ~/.dms)."""
    monkeypatch.setenv("DMS_CLI_HOME", str(tmp_path / ".dms"))
    monkeypatch.delenv("DMS_TOKEN", raising=False)
    monkeypatch.delenv("DMS_GATEWAY_URL", raising=False)
    return tmp_path


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def logged_in(cli_home) -> credentials.Credentials:
    creds = credentials.Credentials(
        gateway_url="http://gateway.test",
        access_token="access-token",
        refresh_token="refresh-token",
        username="alice",
    )
    credentials.save_credentials(creds)
    return creds


@pytest.fixture
def mock_transport_factory(monkeypatch):
    """Patcht `GatewayClient.__init__` so, dass jede ueber `context.get_client()`
    gebaute Instanz den uebergebenen `httpx.MockTransport` statt echtem Netzwerk
    verwendet - trifft alle Command-Module gleichermassen, da sie dieselbe
    `GatewayClient`-Klasse referenzieren (keine Notwendigkeit, jedes Modul
    einzeln zu patchen)."""

    def _install(handler) -> httpx.MockTransport:
        mock_transport = httpx.MockTransport(handler)
        original_init = GatewayClient.__init__

        def patched_init(self, creds, *, transport=None, on_refresh=None):  # noqa: ARG001
            original_init(self, creds, transport=mock_transport, on_refresh=on_refresh)

        monkeypatch.setattr(GatewayClient, "__init__", patched_init)
        return mock_transport

    return _install


def json_response(status_code: int, body) -> httpx.Response:
    return httpx.Response(status_code, json=body)


@pytest.fixture
def route_json():
    """Baut aus {"METHOD /pfad": body} (oder {"METHOD /pfad": (status, body)})
    einen Handler fuer `mock_transport_factory` - deckt den ueberwiegenden
    Testfall (ein Request pro Aufruf, feste Antwort) ab, ohne den Handler in
    jedem Testmodul neu zu schreiben."""

    def _build(routes: dict):
        def handler(request: httpx.Request) -> httpx.Response:
            key = f"{request.method} {request.url.path}"
            if key not in routes:
                raise AssertionError(f"Unerwarteter Request: {key}")
            value = routes[key]
            status_code, body = value if isinstance(value, tuple) else (200, value)
            if body is None:
                return httpx.Response(status_code)
            return httpx.Response(status_code, json=body)

        return handler

    return _build
