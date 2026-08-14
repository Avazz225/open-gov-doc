from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "reporting-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    workflow_service_base_url: str = "http://localhost:8014"
    audit_service_base_url: str = "http://localhost:8002"
    storage_service_base_url: str = "http://localhost:8005"
    notification_service_base_url: str = "http://localhost:8015"
    # RBAC (Post-Roadmap Phase 19 Session 7, ADR 0072) - reporting-service
    # previously had no permission check at all, not even for the forensic
    # trace.
    permission_service_base_url: str = "http://localhost:8004"
    # Externally reachable gateway address for the download link in
    # scheduled report emails (5.4a) - deliberately separate from
    # `self_address` (internal Docker DNS address for registry
    # self-registration, unreachable for an email recipient from outside).
    # Same principle as `NEXT_PUBLIC_GATEWAY_BASE_URL` in the frontend apps.
    gateway_base_url: str = "http://localhost:8009"

    # Which subjects are consumed (only document.created is relevant for the
    # document volume read model, see consumer.py).
    subjects: list[str] = ["document.>"]

    # Poll interval for due report schedules (5.4a) - same idiom as
    # document-service's _retention_poll_loop/workflow-service's
    # _sla_poll_loop. Deliberately low enough for tests/smoke verification,
    # can be raised via env in production.
    report_poll_interval_seconds: int = 3600

    # Forensic trace anomaly indicator (5.4b, since P7-S2c) - only rule type
    # per concept ("initially exclusively via configurable static
    # thresholds"): more than `anomaly_download_threshold_count` downloads
    # by the same actor within `anomaly_download_threshold_minutes` minutes.
    anomaly_download_threshold_count: int = 20
    anomaly_download_threshold_minutes: int = 5
