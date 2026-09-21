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

    # SSRF guard escape hatch (Phase 59 Session 5) - `_validate_peer_base_url`
    # rejects loopback/private/link-local targets by default. This project's
    # OWN test suite deliberately pairs an installation with itself via
    # `http://localhost:8000` (the "self-loopback" smoke test, see
    # docs/services/migration-service.md "Deliberate limitations" - no real
    # second installation is feasible in this sandbox), which would
    # otherwise always be rejected. `False` in production; the docker-compose
    # dev/test stack sets this `True` explicitly, documented there as a
    # test-only relaxation, never something a real installation should need.
    allow_loopback_peers: bool = False
