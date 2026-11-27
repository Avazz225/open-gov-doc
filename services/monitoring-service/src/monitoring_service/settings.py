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

    # Central log levels (P97-S1, ADR 0267): DEBUG always expires - it can
    # log personal data and costs volume, and a forgotten DEBUG is a classic.
    # Without an explicit duration it lasts the default, at most the maximum.
    log_level_debug_default_minutes: int = 60
    log_level_debug_max_minutes: int = 1440
    # How often expired entries are removed and this service applies its own
    # levels (it hands the configuration out and doesn't poll itself).
    log_level_refresh_seconds: float = 15.0

    # Component inventory (P100-S1, ADR 0271): the frontends are static sites
    # that don't register; their npm components file is read from these URLs
    # (compose service names; Helm sets its own).
    frontend_component_urls: dict[str, str] = {
        name: f"http://{name}/META-INF/components.cdx.json"
        for name in (
            "admin-ui",
            "user-ui",
            "reviewer-ui",
            "process-designer",
            "migration-console",
            "office-addin",
        )
    }
    component_inventory_cache_seconds: float = 60.0
