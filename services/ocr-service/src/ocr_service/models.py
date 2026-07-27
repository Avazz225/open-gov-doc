from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("ocr")


class OcrResult(Base):
    """OCR-Ergebnis zu genau einer Dokumentversion (3.9). Anders als
    rendering-service's `Rendition` (mehrere Zeilen je Version, eine je
    anwendbarer Regel) gibt es hier bewusst nur ein autoritatives Ergebnis je
    Version - der natürliche Primärschlüssel ist deshalb
    `{document_id}:{version_number}` ohne zusätzlichen Diskriminator."""

    __tablename__ = "ocr_result"

    id: Mapped[str] = mapped_column(String(300), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # "ready" | "needs_review" | "failed"
    engine: Mapped[str] = mapped_column(String(32))  # "native_text_layer" | "tesseract" | ""
    average_confidence: Mapped[float] = mapped_column(Float)
    full_text: Mapped[str] = mapped_column(Text)
    # [{"page_number": 1, "width": ..., "height": ..., "words": [{"text","left",
    #   "top","width","height","confidence"}]}]
    pages: Mapped[list] = mapped_column(JSON)
    # Nur für PDFs gesetzt - rendering-service rastert keine PDFs, Rasterbilder
    # verweisen stattdessen auf ihre bestehende rendering-service-Thumbnail-Rendition.
    page_image_storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
