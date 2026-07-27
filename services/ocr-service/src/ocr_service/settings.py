from dms_common import BaseServiceSettings


class Settings(BaseServiceSettings):
    service_name: str = "ocr-service"

    postgres_dsn: str = "postgresql+asyncpg://dms:dms_dev_only@localhost:5432/dms"

    # Kein Zugriff auf das Document-Service-Schema/-Storage-Key direkt (3.1) -
    # Metadaten und Originalinhalt werden ausschließlich über dessen HTTP-API
    # bezogen (GET .../versions/{n} bzw. .../versions/{n}/content).
    document_service_base_url: str = "http://localhost:8006"

    # OCR speichert eigene Seitenbilder (PDFs) über den Storage Service - nur
    # dort, nie im OCR Service selbst (3.6, gleiches Prinzip wie rendering-service).
    storage_service_base_url: str = "http://localhost:8005"

    # Gleiches Muster wie rendering-service: ein breites document.>-Abo statt
    # zweier Einzel-Subscriptions, Dispatch nach event_type im Consumer.
    document_subjects: list[str] = ["document.>"]

    # DPI der Seitenrasterung - sowohl Eingabe für Tesseract als auch
    # eigenständiges Seitenbild (rendering-service rastert keine PDFs).
    raster_dpi: int = 150

    # "Nutzbarer Textlayer" (3.9: automatische Erkennung, ob OCR überhaupt
    # nötig ist) ab dieser Zeichenzahl auf Seite 1 - einfache, bewusst grobe
    # Schwelle, filtert leere/dekorative Deckblätter ohne kurze echte Seiten
    # (z. B. ein einzeiliges Deckblatt) falsch einzuordnen.
    min_native_text_chars: int = 20

    # Unterhalb dieser mittleren Tesseract-Konfidenz gilt ein Ergebnis als
    # `needs_review` (3.9: optionale manuelle Nachprüfung bei niedriger
    # Konfidenz) - rein informativ, blockiert nichts (siehe ADR 0010: nur der
    # Virenscan gated den Zugriff).
    needs_review_confidence_threshold: float = 70.0
