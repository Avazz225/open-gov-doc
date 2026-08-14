import uuid
from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("reporting")


class DocumentCreatedEvent(Base):
    """Read model for the document volume report (5.4a) - one row per
    consumed `document.created` event. Deliberately insert-only (no
    update/delete on later deletion of the document): "volume" means
    inflow over time, not the current stock - a later deletion does not
    change the fact that the document was created in this period/folder."""

    __tablename__ = "document_created_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id: Mapped[str] = mapped_column(String(128))
    folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ReportSchedule(Base):
    """Schedulable, recurring report delivery (5.4a "schedulable (regular
    sending via the Notification Service)") - `_report_schedule_poll_loop`
    (main.py) regenerates the report when due."""

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
    """An actually generated, persisted report run - only for scheduled
    deliveries (ad-hoc exports via `.../export` are not persisted, see
    docs/services/reporting-service.md). The actual content lives in the
    Storage Service, only the reference is kept here (3.6 principle)."""

    __tablename__ = "report_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    schedule_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    report_type: Mapped[str] = mapped_column(String(32))
    format: Mapped[str] = mapped_column(String(8))
    storage_object_key: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(64))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
