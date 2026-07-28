from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, ForeignKey, String
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


class ObjectTypeLayout(Base):
    """Formular-Layout je Objekttyp und Verwendungszweck (2.2b, seit P5b-S2):
    ``purpose`` ist ``"display"``|``"search"``|``"upload"``. Nur explizit vom
    generierten "Smart Layout" abweichende Layouts werden hier persistiert
    (siehe ``object_type_service.layout.generate_smart_layout``) - ohne eigene
    Zeile wird bei jedem Lesezugriff aus der aktuellen Attributliste neu
    generiert, damit ein unveränderter Default nie veraltet."""

    __tablename__ = "object_type_layout"

    object_type_id: Mapped[int] = mapped_column(
        ForeignKey("object_type.object_type.id", ondelete="CASCADE"), primary_key=True
    )
    purpose: Mapped[str] = mapped_column(String(16), primary_key=True)
    layout: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
