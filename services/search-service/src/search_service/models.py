from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("search")


class SearchDocument(Base):
    """An index entry per document (not per version, unlike `Rendition`/
    `OcrResult`) - search reflects the current state, not the history.
    `attributes` deliberately uses `postgresql.JSONB` instead of the generic
    `JSON` that document-service/ocr-service use: this is its own, new
    model without migration ties, JSONB cleanly supports the `->>`
    operations needed for attribute filters."""

    __tablename__ = "search_document"

    document_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(1024))
    folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    folder_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    object_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    current_version_number: Mapped[int] = mapped_column(Integer)
    # Full text from OCR (preferred) or substitute_text rendition (fallback)
    # - empty as long as neither is available (see consumer.py).
    full_text: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # Maintained in Python/SQL (repository.upsert_document), not a generated
    # column - keeps the weighting logic (title > full text) visible/testable
    # instead of hidden in the DDL.
    search_vector: Mapped[str] = mapped_column(TSVECTOR)

    __table_args__ = (
        Index("ix_search_document_search_vector", "search_vector", postgresql_using="gin"),
    )
