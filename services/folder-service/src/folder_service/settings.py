from dms_common import BaseServiceSettings

ROOT_FOLDER_ID = "root"

# Inbox/Outbox (2.5/3.3, P15-S3): further fixed special folders following
# the same pattern as ROOT_FOLDER_ID (a plain, well-known ID instead of a
# UUID, no separate `kind` discriminator - see
# `repository.ensure_special_folders`). All three special folders are
# protected against renaming/moving/deletion (see main.py), since concept
# §2.5 describes a special area as "existing exactly once per
# installation" - `root` was deliberately excluded here until Post-Roadmap
# Phase 19 Session 11 (see ADR 0076), this gap has since been closed.
INBOX_FOLDER_ID = "inbox"
OUTBOX_FOLDER_ID = "outbox"
SPECIAL_FOLDER_IDS = frozenset({ROOT_FOLDER_ID, INBOX_FOLDER_ID, OUTBOX_FOLDER_ID})
PROTECTED_FOLDER_IDS = frozenset({ROOT_FOLDER_ID, INBOX_FOLDER_ID, OUTBOX_FOLDER_ID})


class Settings(BaseServiceSettings):
    service_name: str = "folder-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Object-type validation is optional - only invoked when a folder
    # actually carries an object_type_id (2.2).
    object_type_service_base_url: str = "http://localhost:8007"

    # Retention/legal hold/forced deletion for folders (5.2/5.2a, since
    # P7-S1b) - trash/restore cascade synchronously onto contained
    # documents (see document_client.py), forced deletion queries via this
    # whether the subtree still contains active documents.
    document_service_base_url: str = "http://localhost:8006"

    # Generic four-eyes approval mechanism (4.3) - same pattern as
    # document-service since P7-S1, here for the action type
    # `folder.force_delete`.
    permission_service_base_url: str = "http://localhost:8004"

    # Sensor concept (10.1, full rollout): monitoring-service's
    # `/sensor-config`, queried by `SensorConfigClient` to decide whether the
    # generic http.* sensors record with zero overhead when deactivated.
    monitoring_service_base_url: str = "http://localhost:8026"

    # Which subjects folder-service consumes (since P7-S1b) - first
    # consumer of this service at all, previously a pure producer.
    subjects: list[str] = ["permission.approval.approved"]

    # Poll interval of `_retention_poll_loop` (main.py) - same idiom as
    # document-service's `retention_poll_interval_seconds` (P7-S1).
    retention_poll_interval_seconds: float = 3600.0

    # Deletion reconciliation after restore (10.4, P11-S4) - same role as
    # document-service's `kennzeichen_admin_role`, here for the new
    # `POST /folders/{id}/reconcile-restore-deletion` endpoint.
    admin_role: str = "dms-admin"

    # Trash family (2.5, P15-S1): permanent deletion from trash is reserved
    # for a separate, domain-specific admin role (4.6) - deliberately
    # independently configurable instead of reusing `admin_role`, same
    # principle as document-service's separate role settings. No classified
    # documents variant here - concept 2.5 mentions the classification
    # explicitly only for documents, not for folders.
    trash_hard_delete_admin_role: str = "dms-admin"
