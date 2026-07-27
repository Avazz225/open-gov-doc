from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("search")


class SearchDocument(Base):
    """Ein Index-Eintrag je Dokument (nicht je Version, anders als `Rendition`/
    `OcrResult`) - Suche bildet den aktuellen Stand ab, nicht die Historie.
    `attributes` nutzt bewusst `postgresql.JSONB` statt des generischen `JSON`,
    das document-service/ocr-service verwenden: dies ist ein eigenes, neues
    Modell ohne Migrationsbindung, JSONB unterstützt die für Attributfilter
    nötigen `->>`-Operationen sauber."""

    __tablename__ = "search_document"

    document_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    title: Mapped[str] = mapped_column(String(1024))
    folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    folder_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    object_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    attributes: Mapped[dict] = mapped_column(JSONB, default=dict)
    current_version_number: Mapped[int] = mapped_column(Integer)
    # Volltext aus OCR (bevorzugt) oder substitute_text-Rendition (Fallback) -
    # leer, solange keines von beidem vorliegt (siehe consumer.py).
    full_text: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    indexed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # In Python/SQL gepflegt (repository.upsert_document), keine generierte
    # Spalte - hält die Gewichtungslogik (Titel > Volltext) sichtbar/testbar
    # statt in der DDL versteckt.
    search_vector: Mapped[str] = mapped_column(TSVECTOR)

    __table_args__ = (
        Index("ix_search_document_search_vector", "search_vector", postgresql_using="gin"),
    )
