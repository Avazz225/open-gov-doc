from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "search-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    document_service_base_url: str = "http://localhost:8006"
    folder_service_base_url: str = "http://localhost:8008"
    object_type_service_base_url: str = "http://localhost:8007"
    permission_service_base_url: str = "http://localhost:8004"
    ocr_service_base_url: str = "http://localhost:8012"
    rendering_service_base_url: str = "http://localhost:8011"
    monitoring_service_base_url: str = "http://localhost:8026"

    # Two separate consumer subscriptions (see consumer.py): metadata
    # events from the Document Service and full-text follow-up deliveries
    # from OCR/Rendering.
    document_subjects: list[str] = ["document.>"]
    ocr_subjects: list[str] = ["ocr.>"]
    rendering_subjects: list[str] = ["rendering.>"]

    # Confidence threshold for a "usable" OCR result - needs_review still
    # yields searchable text, only failed does not.
    search_result_overfetch_factor: int = 3
    search_result_hard_limit: int = 300
