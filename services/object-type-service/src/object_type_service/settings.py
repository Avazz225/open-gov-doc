from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "object-type-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    monitoring_service_base_url: str = "http://localhost:8026"
