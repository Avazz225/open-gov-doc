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
    # that don't register. Their full components file (npm plus the Alpine
    # packages of the nginx image) is read from an internal-only port 8081 that
    # is never published (P100-S3); port 80 serves the npm part only. Compose
    # service names; Helm sets its own.
    frontend_component_urls: dict[str, str] = {
        name: f"http://{name}:8081/META-INF/components.cdx.json"
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

    # Vulnerability checks (P100-S2, ADR 0272). OG Doc ships no vulnerability
    # data; online, a record is fetched live from OSV (or a mirror) - off by
    # default, since many installations are air-gapped. Proxies come from the
    # usual HTTPS_PROXY/NO_PROXY variables. Only the vulnerability id leaves the
    # installation, never the inventory.
    vulnerability_lookup_enabled: bool = False
    vulnerability_lookup_url: str = "https://api.osv.dev"
    vulnerability_lookup_timeout_seconds: float = 20.0
    # Scanning the whole inventory (P100-S3). Online it is an OSV batch query
    # with every installed package and version - the package list leaves the
    # installation, hence its own switch (user decision 2026-11-27), also off
    # by default. Offline it uses an imported OSV database. The scheduler scans
    # every `vulnerability_scan_interval_hours` (0 = only on demand) when a
    # source is available.
    vulnerability_scan_online_enabled: bool = False
    vulnerability_scan_interval_hours: float = 24.0
    vulnerability_scan_check_seconds: float = 900.0
    vulnerability_scan_concurrency: int = 8
    vulnerability_scan_keep: int = 20
    # Largest database upload (a pre-filtered CLI upload is small; OSV's npm
    # dump is over 200 MB - import that one with the CLI).
    vulnerability_import_max_mb: int = 120
