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


def test_env_override(monkeypatch):
    monkeypatch.setenv("DMS_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("DMS_POSTGRES_DSN", "postgresql+asyncpg://u:p@host/db")
    settings = ExampleSettings(_env_file=None)
    assert settings.log_level == "DEBUG"
    assert settings.postgres_dsn == "postgresql+asyncpg://u:p@host/db"
