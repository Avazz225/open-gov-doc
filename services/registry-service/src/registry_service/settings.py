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

    # Periodic cleanup of permanently-unreachable instance rows (3.2a, Phase
    # 58 Session 2) - a much longer window than `heartbeat_timeout_seconds`
    # (which only decides routing eligibility, not row lifetime). Default 7
    # days: long enough that a genuinely temporary outage/redeploy never
    # gets cleaned up, short enough that stale rows from a permanently
    # decommissioned instance don't accumulate indefinitely in the admin
    # UI's registry overview.
    unreachable_cleanup_after_seconds: float = 604800.0
    cleanup_poll_interval_seconds: float = 3600.0

    # Operator gate for drain/activate (10.5/3.8, Phase 59 Session 4) - same
    # bearer-secret pattern as `federation-hub-service`'s `hub_operator_key`
    # (ADR 0039): `None` by default, fully locked (`403`) until an operator
    # deliberately sets it. `scripts/rolling-update.sh` reads the same value
    # from `REGISTRY_OPERATOR_KEY` in the operator's own shell environment.
    registry_operator_key: str | None = None

    # Branding config (7.3/8, P69-S2, ADR 0201) - registry-service's first
    # ever RBAC-gated endpoint (`PUT /installation/branding`), reusing the
    # already-established `admin.object_config` capability (same one
    # `workflow-service`'s federation config / BPMN upload and
    # `config-service`'s own import gate use) rather than minting a new,
    # narrower capability for a single write endpoint.
    permission_service_base_url: str = "http://localhost:8004"
