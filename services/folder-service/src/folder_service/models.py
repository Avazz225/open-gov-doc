from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("folder")


class Folder(Base):
    """Ordner als hierarchischer Container (2.1). Publiziert Struktur-Events,
    die der Permission Service konsumiert, um seinen `ResourceNode`-Baum
    synchron zu halten (siehe docs/services/permission-service.md)."""

    __tablename__ = "folder"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    parent_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("folder.folder.id"), nullable=True, index=True
    )
    # Opake Referenz auf object-type-service - keine FK-Erzwingung über
    # Service-Grenzen hinweg (analog zu document-service).
    object_type_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
