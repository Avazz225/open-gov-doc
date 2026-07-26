from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Gemeinsame Konfigurationsbasis. Jeder Service leitet hiervon ab und
    überschreibt mindestens ``service_name`` mit einem festen Default.
    """

    model_config = SettingsConfigDict(env_prefix="DMS_", env_file=".env", extra="ignore")

    service_name: str
    environment: str = "development"
    log_level: str = "INFO"

    postgres_dsn: str | None = None
    nats_url: str = "nats://localhost:4222"
    otel_exporter_endpoint: str | None = None
