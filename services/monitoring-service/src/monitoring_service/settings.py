from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "monitoring-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    registry_service_base_url: str = "http://localhost:8001"
    permission_service_base_url: str = "http://localhost:8004"
    auth_service_base_url: str = "http://localhost:8003"

    monitoring_permission: str = "admin.monitoring"

    # Timeout je Scrape-Ziel beim Zusammenführen (`GET /metrics`, 10.1) - ein
    # einzelnes langsames/unerreichbares Ziel darf die anderen nicht
    # verzögern, siehe `scraper.scrape_and_merge` (paralleler `asyncio.gather`
    # mit `return_exceptions=True`).
    scrape_timeout_seconds: float = 5.0
