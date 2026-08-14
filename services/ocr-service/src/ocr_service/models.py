from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("ocr")


class OcrResult(Base):
    """OCR result for exactly one document version (3.9). Unlike
    rendering-service's `Rendition` (multiple rows per version, one per
    applicable rule), there is deliberately only one authoritative result
    per version here - the natural primary key is therefore
    `{document_id}:{version_number}` without an additional discriminator."""

    __tablename__ = "ocr_result"

    id: Mapped[str] = mapped_column(String(300), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    # "ready" | "needs_review" | "skipped" | "failed" | "failed_permanent"
    # (since Post-Roadmap Phase 20 Session 4, ADR 0080) - "skipped" (word
    # limit/content-type allowlist) is a deliberate decision, never
    # retry-capable.
    status: Mapped[str] = mapped_column(String(16))
    engine: Mapped[str] = mapped_column(String(32))  # "native_text_layer" | "tesseract" | ""
    average_confidence: Mapped[float] = mapped_column(Float)
    full_text: Mapped[str] = mapped_column(Text)
    # [{"page_number": 1, "width": ..., "height": ..., "words": [{"text","left",
    #   "top","width","height","confidence"}]}]
    pages: Mapped[list] = mapped_column(JSON)
    # Only set for PDFs - rendering-service does not rasterize PDFs, raster
    # images instead reference their existing rendering-service thumbnail rendition.
    page_image_storage_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class OcrConfig(Base):
    """Admin-UI-editable runtime configuration (3.9, P5b-S5) - deliberately a
    single row with a fixed `id=1` instead of true multi-row storage, since
    there is exactly one installation per ocr-service deployment (no tenant
    separation within an installation, 3a). Unlike `Settings` (env-only,
    only takes effect on restart), this row is read fresh by
    `pipeline.py`/`consumer.py` for every processed document, so it takes
    effect without a restart. `ocrEnabled` itself is deliberately **not**
    part of this table, but a Docker Compose profile opt-out (see ADR 0016)
    - a service that is already running cannot "undeploy" itself."""

    __tablename__ = "ocr_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # None = no upper limit (default: OCR always runs, as before P5b-S5).
    max_word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    batch_size: Mapped[int] = mapped_column(Integer)
    # Admin-editable content-type allowlist (P5d-S1) - applies on top of the
    # existing technical `select_engine()` selection: if the list is not
    # empty, OCR only runs for the content types listed there, even though
    # `select_engine()` would technically support more (e.g. "only
    # application/pdf, not image/tiff"). Empty list (default) = no
    # restriction, identical to the behavior before P5d-S1.
    allowed_content_types: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
