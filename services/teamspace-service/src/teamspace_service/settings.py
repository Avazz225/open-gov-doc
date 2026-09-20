from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "teamspace-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    folder_service_base_url: str = "http://localhost:8008"
    permission_service_base_url: str = "http://localhost:8004"

    monitoring_service_base_url: str = "http://localhost:8026"

    # P55-S1/ADR 0175: `folder-service`'s structural "this folder is now
    # permanently gone" event - the trigger for orphaned-teamspace cleanup.
    subjects: list[str] = ["folder.resource.deleted"]
