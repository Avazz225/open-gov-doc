import uuid
from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("favorite")


class Favorite(Base):
    """Personal bookmark (quick retrieval, since P7-S1d) on a document or a
    folder. Deliberately without referential checking against the
    document-/folder-service - an orphaned bookmark (e.g. after the original
    is deleted) does no harm; the filter view in the user UI simply
    skips/marks it when resolving, instead of forcing a coupling to other
    services here."""

    __tablename__ = "favorite"
    __table_args__ = (UniqueConstraint("user_id", "object_type", "object_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255))
    object_type: Mapped[str] = mapped_column(String(16))  # "document" | "folder"
    object_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Reminder(Base):
    """Follow-up reminder (Wiedervorlage, P90-S1, ADR 0246): a person asks to
    have a document, folder or case put in front of them again at a set
    time, with a note. Personal like a favorite (keyed by user name, owner
    check on every call, no referential check), but several per object are
    allowed - "in two weeks: check the reply", "in three months: archive".

    `status` is "open" or "done". `notified_at` is when the due notification
    went out; snoozing moves `due_at` and clears it, so the reminder fires
    again."""

    __tablename__ = "reminder"
    __table_args__ = (
        Index("ix_reminder_user_status_due", "user_id", "status", "due_at"),
        # The due poll's own lookup: open, unnotified, due.
        Index("ix_reminder_due_poll", "status", "notified_at", "due_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id: Mapped[str] = mapped_column(String(255))
    object_type: Mapped[str] = mapped_column(String(16))  # "document" | "folder" | "case"
    object_id: Mapped[str] = mapped_column(String(128), index=True)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[str] = mapped_column(String(2000), default="")
    status: Mapped[str] = mapped_column(String(16), default="open")
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
