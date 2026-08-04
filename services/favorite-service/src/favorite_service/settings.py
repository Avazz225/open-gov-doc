from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "favorite-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"
