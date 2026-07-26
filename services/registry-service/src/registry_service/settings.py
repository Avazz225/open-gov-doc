from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "registry-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Konfigurierbares Intervall (3.2a): eine Instanz gilt als ausgefallen, wenn
    # sie länger als heartbeat_timeout_seconds kein Heartbeat mehr gesendet hat.
    heartbeat_timeout_seconds: float = 15.0
