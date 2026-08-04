import uuid
from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("favorite")


class Favorite(Base):
    """Persönliches Lesezeichen (schnelles Wiederfinden, seit P7-S1d) auf ein
    Dokument oder einen Ordner. Bewusst ohne referenzielle Prüfung gegen
    document-/folder-service - ein verwaistes Lesezeichen (z. B. nach
    Löschung des Originals) richtet keinen Schaden an, wird von der
    Filteransicht in der User-UI beim Auflösen einfach übersprungen/markiert
    statt hier eine Kopplung an andere Services zu erzwingen."""

    __tablename__ = "favorite"
    __table_args__ = (UniqueConstraint("user_id", "object_type", "object_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255))
    object_type: Mapped[str] = mapped_column(String(16))  # "document" | "folder"
    object_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
