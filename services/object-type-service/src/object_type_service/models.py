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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
