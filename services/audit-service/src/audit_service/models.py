from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("audit")


class AuditEvent(Base):
    """Ein unveränderlicher, hash-verketteter Audit-Eintrag (Konzept 5.3).

    ``hash`` = sha256(``prev_hash`` + kanonisches JSON der übrigen Felder) -
    jede nachträgliche Änderung eines Eintrags bricht die Kette ab dieser
    Stelle, was ``verify_chain`` erkennt (Manipulationssicherheit auch bei
    direktem DB-Zugriff, siehe 5.3).
    """

    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    event_type: Mapped[str] = mapped_column(String(255), index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    service_name: Mapped[str] = mapped_column(String(128))
    subject: Mapped[str | None] = mapped_column(String(255), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), unique=True)
