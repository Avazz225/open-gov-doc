from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "migration-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    document_service_base_url: str = "http://localhost:8006"
    folder_service_base_url: str = "http://localhost:8008"
    permission_service_base_url: str = "http://localhost:8004"
    workflow_service_base_url: str = "http://localhost:8014"

    monitoring_service_base_url: str = "http://localhost:8026"

    # Four-eyes principle (4.3, P6-S4 pattern): checks before starting a transfer
    # whether "migration.transfer.start" currently requires approval.
    approval_action_type: str = "migration.transfer.start"

    # License brokering (9.1/9.3, P9-S2 pattern) - concept 9.1 names
    # "Migration-Service" literally as an example of a separately licensable
    # component.
    license_status_cache_ttl_seconds: float = 30.0

    # Configurable transition/retention period (7.2) - default for new
    # transfers if no explicit value is given when creating one.
    default_retention_days: int = 30

    # Timeout for calls against a paired target installation (copying
    # potentially many documents, see docs/services/migration-service.md).
    peer_call_timeout_seconds: float = 300.0
