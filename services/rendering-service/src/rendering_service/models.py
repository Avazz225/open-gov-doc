from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("rendering")


class Rendition(Base):
    """A generated preview/rendition for exactly one document version
    (2.4/3.7). The primary key is deliberately a natural key made of
    `document_id`/`version_number`/`rendition_type` instead of a random
    UUID: the same version of the same source format always produces the
    same row when reprocessed (e.g. NATS redelivery) - `repository.
    upsert_rendition` overwrites instead of duplicating.
    """

    __tablename__ = "rendition"

    id: Mapped[str] = mapped_column(String(300), primary_key=True)
    document_id: Mapped[str] = mapped_column(String(128), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    rendition_type: Mapped[str] = mapped_column(String(64))
    source_filename: Mapped[str] = mapped_column(String(512))
    source_content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_filename: Mapped[str] = mapped_column(String(512))
    target_content_type: Mapped[str] = mapped_column(String(255))
    size_bytes: Mapped[int] = mapped_column(Integer)
    storage_object_key: Mapped[str] = mapped_column(String(1024))
    # "ready" | "failed" | "failed_permanent" (since post-roadmap phase 20
    # session 4, ADR 0080).
    status: Mapped[str] = mapped_column(String(16))
    error_message: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
