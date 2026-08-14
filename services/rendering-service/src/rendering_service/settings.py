from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "rendering-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Rendering Service hält keine Dateiinhalte selbst (3.6) - Originale werden
    # über den Document Service abgerufen, Ersatzdarstellungen/Vorschauen über
    # den Storage Service abgelegt.
    storage_service_base_url: str = "http://localhost:8005"

    # Kein Zugriff auf das Document-Service-Schema/-Storage-Key direkt (3.1) -
    # Metadaten und Originalinhalt werden ausschließlich über dessen HTTP-API
    # bezogen (GET .../versions/{n} bzw. .../versions/{n}/content).
    document_service_base_url: str = "http://localhost:8006"

    # Abonnierte Subjects für die automatische Ersatzdarstellungs-Pipeline
    # (2.4): "document.>" statt zweier Einzel-Subscriptions, weil ein Subject
    # je Aufruf einen eigenen JetStream-Durable-Consumer-Namen bräuchte -
    # stattdessen wird im Consumer-Handler nach event_type gefiltert (siehe
    # permission-service structure_consumer.py für dasselbe Muster).
    document_subjects: list[str] = ["document.>"]

    # Nachzieheffekt aus P5-S3 (2.4/3.9): OCR-Volltext speist eine
    # substitute_text-Ersatzdarstellung für Dokumente, die diese Session
    # mangels OCR nicht bedienen konnte (gescannte/bildbasierte Dokumente).
    ocr_service_base_url: str = "http://localhost:8012"
    ocr_subjects: list[str] = ["ocr.completed"]

    # RBAC (Post-Roadmap Phase 19 Session 8, ADR 0073) - rendering-service
    # hatte bislang gar keine Berechtigungsprüfung.
    permission_service_base_url: str = "http://localhost:8004"

    # Retry/Backoff (Post-Roadmap Phase 20 Session 4, ADR 0080) - gleicher
    # Zahlenwert wie archival-/notification-/ocr-service's `max_*_attempts`.
    # Nach Erschoepfung wechselt eine Rendition auf `failed_permanent`.
    max_rendering_attempts: int = 5

    # Poll-Intervall des neuen `_rendition_retry_poll_loop` (main.py) - gleicher
    # Wert wie notification-/ocr-service's `*_retry_poll_interval_seconds`.
    rendering_retry_poll_interval_seconds: float = 60.0
