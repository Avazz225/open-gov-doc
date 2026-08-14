from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "registry-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Configurable interval (3.2a): an instance is considered failed if it has
    # not sent a heartbeat for longer than heartbeat_timeout_seconds.
    heartbeat_timeout_seconds: float = 15.0

    # License brokering (concept 3.2b/9.3, P9-S2). Only `service_type` values
    # listed here count as separately licensable "application components"
    # (9.1) - every other service remains "core" and always gets
    # license_status="licensed". Policy per component: "demo" (read-only
    # access) or "lock" (full lockout). `webdav-connector` (P12-S1) is the
    # first connector to follow this pattern - concept 3.3 names connectors
    # verbatim as an example of licensable components. `migration-service`
    # (P12-S2) follows the same pattern - concept 9.1 names "migration
    # service" verbatim as an example. `cmis-connector` (P12-S4) likewise -
    # concept 9.1 names "CMIS connector" verbatim as an example.
    license_service_base_url: str = "http://localhost:8023"
    license_status_cache_ttl_seconds: float = 60.0
    licensable_components: dict[str, str] = {
        "workflow-service": "demo",
        "webdav-connector": "demo",
        "migration-service": "demo",
        "cmis-connector": "demo",
    }

    # Sensor concept (10.1, P11-S1): registry-service is itself one of the
    # two pilots (no full retrofit, see P11-S0 finding). Activation status
    # comes from `monitoring-service`, not from here.
    monitoring_service_base_url: str = "http://localhost:8026"
    sensor_sample_interval_seconds: float = 15.0
