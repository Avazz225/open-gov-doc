from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("storage")


class ObjectMetadata(Base):
    """Metadaten je gespeichertem Objekt - der eigentliche Inhalt liegt nie
    in der Shared DB, nur Referenz, Prüfsumme und Größe (Konzept 3.6)."""

    __tablename__ = "object_metadata"

    object_key: Mapped[str] = mapped_column(String(1024), primary_key=True)
    backend: Mapped[str] = mapped_column(String(32))
    checksum_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
