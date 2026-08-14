from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "ocr-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # No direct access to the document service's schema/storage key (3.1) -
    # metadata and original content are obtained exclusively via its HTTP
    # API (GET .../versions/{n} or .../versions/{n}/content).
    document_service_base_url: str = "http://localhost:8006"

    # OCR stores its own page images (PDFs) via the storage service - only
    # there, never in the OCR service itself (3.6, same principle as
    # rendering-service).
    storage_service_base_url: str = "http://localhost:8005"

    # RBAC (Post-Roadmap Phase 19 Session 8, ADR 0073) - ocr-service had no
    # permission check at all until now.
    permission_service_base_url: str = "http://localhost:8004"

    # Same pattern as rendering-service: one broad document.> subscription
    # instead of two separate ones, dispatch by event_type in the consumer.
    document_subjects: list[str] = ["document.>"]

    # DPI for page rasterization - both the input for Tesseract and the
    # standalone page image (rendering-service does not rasterize PDFs).
    raster_dpi: int = 150

    # "Usable text layer" (3.9: automatic detection of whether OCR is even
    # needed) from this character count on page 1 - a simple, deliberately
    # coarse threshold that filters out empty/decorative cover pages without
    # misclassifying short genuine pages (e.g. a single-line cover page).
    min_native_text_chars: int = 20

    # Below this average Tesseract confidence, a result counts as
    # `needs_review` (3.9: optional manual review at low confidence) - purely
    # informational, blocks nothing (see ADR 0010: only the virus scan gates
    # access).
    needs_review_confidence_threshold: float = 70.0

    # Retry/backoff (Post-Roadmap Phase 20 Session 4, ADR 0080) - same
    # numeric value as archival-/notification-service's `max_*_attempts`.
    # After exhaustion, an OCR result switches to `failed_permanent`.
    max_ocr_attempts: int = 5

    # Poll interval of the new `_ocr_retry_poll_loop` (main.py) - same value
    # as notification-service's `notification_retry_poll_interval_seconds`.
    ocr_retry_poll_interval_seconds: float = 60.0
