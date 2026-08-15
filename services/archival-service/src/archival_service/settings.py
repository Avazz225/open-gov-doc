from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "archival-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    document_service_base_url: str = "http://localhost:8006"
    rendering_service_base_url: str = "http://localhost:8011"
    storage_service_base_url: str = "http://localhost:8005"
    object_type_service_base_url: str = "http://localhost:8007"
    case_service_base_url: str = "http://localhost:8016"
    # RBAC (Post-Roadmap Phase 19 Session 7, ADR 0072) - archival-service
    # previously had no general permission check at all (only
    # `archive_retrieval_role` below, a separate, narrower gate for
    # retrieval).
    permission_service_base_url: str = "http://localhost:8004"

    # Poll interval for due records-disposal/dehydration ticks (5.6) - the
    # same idiom as document-service's `_retention_poll_loop`/
    # reporting-service's `_report_schedule_poll_loop`.
    archival_poll_interval_seconds: int = 3600

    # Retry/backoff (Post-Roadmap Phase 20 Session 2, ADR 0078) - same
    # numeric value as storage-service's already-existing
    # `max_replication_attempts`, shared here across both transfer types.
    # After exhaustion, the transfer switches to `failed_permanent`.
    max_archival_attempts: int = 5

    # Transition period (5.6) between successful archiving ("released") and
    # removal of the live storage copy ("dehydrated") - makes an accidental
    # disposal easily reversible without immediately requiring the full
    # retrieval process. Same pattern as `TrashConfig.restore_period_days`
    # in document-service.
    dehydration_delay_days: int = 30

    # Role gate for retrieval (5.6, "decryption only for authorized roles")
    # as well as - since P15-S5 - for the read-only records-disposal access
    # area (`GET /released-items`, 2.5): concept 2.5 names "a dedicated
    # archive/registry role" for this; the already-existing retrieval role
    # covers this, no second setting needed. Same pattern as
    # `storage_service.governance_bypass_role`/
    # `document_service.kennzeichen_admin_role`.
    archive_retrieval_role: str = "dms-admin"

    # Dev/test key for `EnvKeyStore` (ADR 0029: only the `KeyStore`
    # interface is shipped, no real KDBX key management). 32 bytes,
    # base64-encoded - AES-256-GCM expects exactly this length.
    archive_encryption_key: str | None = None

    monitoring_service_base_url: str = "http://localhost:8026"
