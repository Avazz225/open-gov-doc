from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "document-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Document Service does not hold any file content itself, but
    # instead exclusively talks to the Storage Service's HTTP API (3.6) - no
    # import of its internals, pure service-to-service communication.
    storage_service_base_url: str = "http://localhost:8005"

    # Since P3-S3: existence check of the folder (2.1) and/or
    # constraint validation of the attributes against the object type
    # (2.2/4.5) - both optional, only when `folder_id`/`object_type_id` are
    # set during creation.
    folder_service_base_url: str = "http://localhost:8008"
    object_type_service_base_url: str = "http://localhost:8007"

    # Mandatory virus scan before an upload is released (10.3, ADR
    # 0010) - called synchronously before content/metadata are persisted.
    virus_scan_service_base_url: str = "http://localhost:8010"

    # Timeout without activity after which an editing lock is
    # automatically considered expired (4.2) - no background sweep needed,
    # see repository.py.
    default_lock_timeout_seconds: float = 1800.0

    # First genuine role check in the entire system (P5e-S2): only
    # principals whose `X-DMS-Roles` header injected by the gateway contains
    # this role may subsequently change an already assigned reference number
    # (attributes["Kennzeichen"]) - for everyone else it is read-only.
    kennzeichen_admin_role: str = "dms-admin"

    # Generic four-eyes-principle approval mechanism (4.3, P6-S4) -
    # force-unlock checks here whether approval is required, and creates an
    # approval request if needed instead of executing immediately.
    permission_service_base_url: str = "http://localhost:8004"

    # Which subjects document-service consumes (P6-S4) - the first
    # consumer of this service at all, previously a pure producer.
    # Force-unlock AND (since P7-S1) forced deletion react here to approved
    # approval requests, see consumer.py.
    subjects: list[str] = ["permission.approval.approved"]

    # Retention/legal hold/forced deletion (5.2/5.2a, since P7-S1) -
    # poll interval of `_retention_poll_loop` (main.py), same idiom as
    # workflow-service's `sla_poll_interval_seconds` (ADR 0020). Date-based
    # deadlines don't need minute-level resolution, default is coarse
    # accordingly.
    retention_poll_interval_seconds: float = 3600.0

    # Role with which document-service identifies itself to the
    # Storage Service when it deliberately bypasses an active governance-mode
    # lock in the course of a sanctioned forced deletion (5.1/5.2a) - must
    # match `storage-service`'s `governance_bypass_role` (default `dms-admin`
    # on both sides, independently configurable like `kennzeichen_admin_role`).
    governance_bypass_role: str = "dms-admin"

    # License limit block on new creations (concept 9.3, P9-S2):
    # "prevents new items until the limit is met again or the license is
    # extended" - existing documents/versions/restoration remain unaffected
    # (check only in `POST /documents`). Fail-open (TTL cache) if
    # license-service is unreachable, same convention as every other
    # cross-service cache in the project.
    license_service_base_url: str = "http://localhost:8023"
    license_limit_cache_ttl_seconds: float = 30.0

    # Sensor concept (10.1, P11-S1): document-service is one of the
    # two pilots (no full retrofit, see P11-S0 finding). Activation status
    # comes from `monitoring-service`.
    monitoring_service_base_url: str = "http://localhost:8026"
    sensor_sample_interval_seconds: float = 15.0

    # Public share link (4.2a, P14-S10): who may revoke a link
    # early without being its creator - same independently configurable
    # role-setting pattern as `kennzeichen_admin_role`/`governance_bypass_role`,
    # even though the default is identical.
    share_link_revoke_admin_role: str = "dms-admin"

    # Direct Office editing (post-roadmap feature, WebDAV edit
    # token): deliberately sized more generously than a share link (hours
    # instead of minutes) - an Office editing session makes WebDAV requests
    # (lock/read/cache/unlock) throughout the entire duration it is open,
    # not just once at the start.
    webdav_edit_token_ttl_hours: float = 8.0

    # Trash family (2.5, P15-S1): permanent deletion from the trash
    # is reserved for two domain-separated admin roles (4.6, same setting
    # pattern as above) - one regular and one narrower one for documents
    # classified as classified documents (`ObjectType.classification_level`,
    # multi-level since P17-S2), which are not necessarily staffed by the
    # same people.
    trash_hard_delete_admin_role: str = "dms-admin"
    classified_trash_hard_delete_admin_role: str = "dms-admin"

    # Quarantine area (2.5/10.3, P15-S2): second, independent role
    # gate for `POST /documents/from-quarantine-release` (internal creation
    # path that deliberately does not trigger another virus scan) - the
    # virus-scan-service already checks the same release permission itself
    # (`quarantine_admin_role`), independently configurable like every other
    # role-setting pair in this project.
    quarantine_release_admin_role: str = "dms-admin"
