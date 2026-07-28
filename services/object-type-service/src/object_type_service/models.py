from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("object_type")


class ObjectType(Base):
    """Objekttyp-Definition (2.2): Attribute, Namenskonventionen, bedingte
    Regeln. Angewandt wird das Schema nicht hier, sondern über
    ``dms-constraint-engine`` im ``/validate``-Endpunkt (siehe main.py)."""

    __tablename__ = "object_type"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    applies_to: Mapped[str] = mapped_column(String(16))  # "document" | "folder"
    attributes: Mapped[list] = mapped_column(JSON, default=list)
    naming_constraints: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    # Erzwungene Objekt-Hierarchie (2.2a): Namen zulässiger Eltern-Ordnerklassen,
    # oder der Sentinel "$ROOT" für Platzierung direkt unter der Wurzel. None/leer
    # = überall platzierbar (Rückwärtskompatibilität zu Typen ohne diese Angabe).
    allowed_parent_types: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Nur für Ordnerklassen (applies_to == "folder") gesetzt (2.2a) - Anzeige im
    # User-UI-Explorer vor dem Namen folgt erst mit P5b-S4.
    icon: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
