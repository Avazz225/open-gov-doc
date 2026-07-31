from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("notification")


class Notification(Base):
    """Ein einzelner Zustellversuch (Konzept 7.1, P6-S2). `recipient` ist je nach
    `channel` heterogen (E-Mail-Adresse / Nutzerkennung-oder-Lane-Name / Webhook-URL) -
    bewusst ein einziges generisches Feld statt dreier kanalspezifischer Spalten, um das
    Modell für dieses Grundgerüst einfach zu halten. Zustellung passiert synchron beim
    Anlegen (`repository.create_and_send`), `status`/`error`/`sent_at` spiegeln das
    Ergebnis - kein Retry (siehe ADR 0020/`docs/services/notification-service.md`)."""

    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(16))
    recipient: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
