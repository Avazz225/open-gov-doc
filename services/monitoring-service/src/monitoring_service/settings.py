from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "monitoring-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    registry_service_base_url: str = "http://localhost:8001"
    permission_service_base_url: str = "http://localhost:8004"
    auth_service_base_url: str = "http://localhost:8003"

    monitoring_permission: str = "admin.monitoring"

    # Timeout per scrape target when merging (`GET /metrics`, 10.1) - a
    # single slow/unreachable target must not delay the others,
    # see `scraper.scrape_and_merge` (parallel `asyncio.gather`
    # with `return_exceptions=True`).
    scrape_timeout_seconds: float = 5.0

    # Alerting (P88-S5, ADR 0245): how often rules are evaluated, and the
    # time zone downtime windows' daily times are read in.
    alert_evaluation_interval_seconds: float = 60.0
    timezone: str = "Europe/Berlin"
