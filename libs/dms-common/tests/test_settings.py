from dms_common.settings import BaseServiceSettings


class ExampleSettings(BaseServiceSettings):
    service_name: str = "example-service"


def test_defaults():
    settings = ExampleSettings(_env_file=None)
    assert settings.service_name == "example-service"
    assert settings.environment == "development"
    assert settings.log_level == "INFO"
    assert settings.nats_url == "nats://localhost:4222"
    assert settings.postgres_dsn is None
    assert settings.installation_id == "local-dev"
    assert settings.installation_display_name == "DMS-Installation (Entwicklung)"


def test_env_override(monkeypatch):
    monkeypatch.setenv("DMS_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DMS_POSTGRES_DSN", "postgresql+asyncpg://u:p@host/db")
    settings = ExampleSettings(_env_file=None)
    assert settings.log_level == "DEBUG"
    assert settings.postgres_dsn == "postgresql+asyncpg://u:p@host/db"


def test_installation_identity_shared_across_services(monkeypatch):
    """3a/P13-S1: eine Installation setzt diese beiden Variablen genau
    einmal, jeder Service leitet denselben Wert über dieselbe
    `BaseServiceSettings`-Basis ab - kein service-eigenes Duplikat mehr."""
    monkeypatch.setenv("DMS_INSTALLATION_ID", "kunde-nord-001")
    monkeypatch.setenv("DMS_INSTALLATION_DISPLAY_NAME", "Kunde Nord GmbH")
    settings = ExampleSettings(_env_file=None)
    assert settings.installation_id == "kunde-nord-001"
    assert settings.installation_display_name == "Kunde Nord GmbH"
