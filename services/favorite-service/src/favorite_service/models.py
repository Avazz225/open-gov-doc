import uuid
from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, String, UniqueConstraint
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
