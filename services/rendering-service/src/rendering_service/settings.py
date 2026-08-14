from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "rendering-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Rendering Service does not hold any file content itself (3.6) - originals
    # are retrieved via the Document Service, renditions/previews are stored
    # via the Storage Service.
    storage_service_base_url: str = "http://localhost:8005"

    # No direct access to the document service schema/storage key (3.1) -
    # metadata and original content are obtained exclusively via its HTTP API
    # (GET .../versions/{n} or .../versions/{n}/content).
    document_service_base_url: str = "http://localhost:8006"

    # Subscribed subjects for the automatic rendition pipeline (2.4):
    # "document.>" instead of two individual subscriptions, because one
    # subject per call would need its own JetStream durable consumer name -
    # instead the consumer handler filters by event_type (see
    # permission-service structure_consumer.py for the same pattern).
    document_subjects: list[str] = ["document.>"]

    # Follow-up effect from P5-S3 (2.4/3.9): OCR full text feeds a
    # substitute_text rendition for documents that this session could not
    # serve due to missing OCR (scanned/image-based documents).
    ocr_service_base_url: str = "http://localhost:8012"
    ocr_subjects: list[str] = ["ocr.completed"]

    # RBAC (Post-Roadmap Phase 19 Session 8, ADR 0073) - rendering-service
    # previously had no permission check at all.
    permission_service_base_url: str = "http://localhost:8004"

    # Retry/backoff (Post-Roadmap Phase 20 Session 4, ADR 0080) - same value
    # as archival-/notification-/ocr-service's `max_*_attempts`.
    # After exhaustion a rendition switches to `failed_permanent`.
    max_rendering_attempts: int = 5

    # Poll interval of the new `_rendition_retry_poll_loop` (main.py) - same
    # value as notification-/ocr-service's `*_retry_poll_interval_seconds`.
    rendering_retry_poll_interval_seconds: float = 60.0
