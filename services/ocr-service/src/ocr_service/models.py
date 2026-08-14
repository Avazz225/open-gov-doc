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
    # "ready" | "needs_review" | "skipped" | "failed" | "failed_permanent" (seit
    # Post-Roadmap Phase 20 Session 4, ADR 0080) - "skipped" (Wortobergrenze/
    # Content-Type-Positivliste) ist eine bewusste Entscheidung, nie retry-fähig.
    status: Mapped[str] = mapped_column(String(16))
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
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OcrConfig(Base):
    """Admin-UI-editierbare Laufzeit-Konfiguration (3.9, P5b-S5) - bewusst eine
    einzelne Zeile mit fester `id=1` statt echter Mehrzeiligkeit, da es genau
    eine Installation je ocr-service-Deployment gibt (keine Mandantentrennung
    innerhalb einer Installation, 3a). Anders als `Settings` (env-only, nur bei
    Neustart wirksam) wird diese Zeile von `pipeline.py`/`consumer.py` bei
    jedem verarbeiteten Dokument frisch gelesen, ist also ohne Neustart wirksam.
    `ocrEnabled` selbst ist bewusst **nicht** Teil dieser Tabelle, sondern ein
    Docker-Compose-Profil-Opt-out (siehe ADR 0016) - ein bereits laufender
    Service kann sich nicht selbst "undeployen"."""

    __tablename__ = "ocr_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # None = keine Obergrenze (Default: OCR läuft immer, wie vor P5b-S5).
    max_word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    batch_size: Mapped[int] = mapped_column(Integer)
    # Admin-editierbare Content-Type-Positivliste (P5d-S1) - greift zusätzlich
    # zur bestehenden technischen `select_engine()`-Auswahl: ist die Liste
    # nicht leer, läuft OCR nur noch für die dort genannten Content-Types,
    # obwohl `select_engine()` weitere technisch unterstützen würde (z. B.
    # "nur application/pdf, nicht image/tiff"). Leere Liste (Default) = keine
    # Einschränkung, identisch zum Verhalten vor P5d-S1.
    allowed_content_types: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
