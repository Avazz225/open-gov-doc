import uuid
from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("reporting")


class DocumentCreatedEvent(Base):
    """Read-Modell fuer den Dokumentenaufkommen-Bericht (5.4a) - eine Zeile
    je konsumiertem `document.created`-Event. Bewusst insert-only (kein
    Update/Delete bei spaeterer Loeschung des Dokuments): "Aufkommen"
    bedeutet Zufluss ueber die Zeit, nicht der aktuelle Bestand - eine
    spaetere Loeschung aendert nichts daran, dass das Dokument in diesem
    Zeitraum/Ordner angelegt wurde."""

    __tablename__ = "document_created_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(128))
    folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReportSchedule(Base):
    """Planbarer, wiederkehrender Berichtsversand (5.4a "planbar (regel-
    mässiger Versand über den Notification Service)") - `_report_schedule_
    poll_loop` (main.py) generiert bei Faelligkeit den Bericht neu."""

    __tablename__ = "report_schedule"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    report_type: Mapped[str] = mapped_column(String(32))
    format: Mapped[str] = mapped_column(String(8))
    frequency: Mapped[str] = mapped_column(String(16))
    recipient_email: Mapped[str] = mapped_column(String(255))
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    next_run_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReportRun(Base):
    """Ein tatsaechlich erzeugter, persistierter Berichtslauf - nur fuer
    geplante Versendungen (Ad-hoc-Exporte ueber `.../export` werden nicht
    persistiert, siehe docs/services/reporting-service.md). Der eigentliche
    Inhalt liegt im Storage Service, hier nur die Referenz (3.6-Prinzip)."""

    __tablename__ = "report_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    schedule_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    report_type: Mapped[str] = mapped_column(String(32))
    format: Mapped[str] = mapped_column(String(8))
    storage_object_key: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
