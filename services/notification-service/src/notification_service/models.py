from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("notification")


class Notification(Base):
    """Ein Zustellversuch (Konzept 7.1, P6-S2). `recipient` ist je nach `channel`
    heterogen (E-Mail-Adresse / Nutzerkennung-oder-Lane-Name / Webhook-URL) -
    bewusst ein einziges generisches Feld statt dreier kanalspezifischer Spalten, um das
    Modell für dieses Grundgerüst einfach zu halten. Der erste Versuch passiert
    synchron beim Anlegen (`repository.create_and_send`) - seit Post-Roadmap Phase 20
    Session 3 (ADR 0079) sind fehlgeschlagene `email`/`webhook`-Zustellungen nicht mehr
    sofort terminal: `status` bleibt `"failed"` (retry-fähig) mit steigendem `attempts`
    und einem per Full-Jitter-Backoff gesetzten `next_retry_at`, den ein neuer,
    eigenständiger Poll-Loop abarbeitet (Zustellung selbst bleibt synchron/inline im
    NATS-Handler bzw. im `POST /notifications`-Endpunkt - nur die WIEDERHOLUNG läuft
    asynchron, um den Handler nicht zu blockieren). Erst nach `max_notification_
    attempts` erfolglosen Versuchen wechselt `status` auf das echte Terminalstatus
    `failed_permanent`. `in_app` hat keinen echten Zustellschritt und ist daher nie
    retry-fähig - immer sofort `"sent"`."""

    __tablename__ = "notification"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel: Mapped[str] = mapped_column(String(16))
    recipient: Mapped[str] = mapped_column(String(512))
    subject: Mapped[str] = mapped_column(String(512))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16))
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
