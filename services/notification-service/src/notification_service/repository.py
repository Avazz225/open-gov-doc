from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service import delivery
from notification_service.models import Notification
from notification_service.settings import Settings


class NotFoundError(Exception):
    pass


async def create_and_send(
    session: AsyncSession,
    settings: Settings,
    *,
    channel: str,
    recipient: str,
    subject: str,
    body: str,
) -> Notification:
    """Persistiert zuerst, versucht dann synchron zuzustellen (Konzept 7.1, P6-S2) -
    das Ergebnis (`status`/`error`/`sent_at`) landet direkt am selben Datensatz, kein
    separater Zustellstatus-Nachtrag, kein Retry (siehe `delivery.py`)."""
    now = datetime.now(UTC)
    notification = Notification(
        channel=channel,
        recipient=recipient,
        subject=subject,
        body=body,
        status="failed",
        error=None,
        created_at=now,
        sent_at=None,
    )
    session.add(notification)
    await session.flush()

    try:
        if channel == "email":
            await delivery.send_email(settings, recipient, subject, body)
        elif channel == "webhook":
            await delivery.send_webhook(recipient, subject, body)
        # "in_app" wird ausschließlich persistiert, keine weitere Zustellung.
        notification.status = "sent"
        notification.sent_at = datetime.now(UTC)
    except delivery.DeliveryError as exc:
        notification.status = "failed"
        notification.error = str(exc)

    await session.flush()
    return notification


async def get_notification(session: AsyncSession, notification_id: int) -> Notification:
    notification = await session.get(Notification, notification_id)
    if notification is None:
        raise NotFoundError(f"notification_id {notification_id!r} unbekannt")
    return notification


async def list_notifications(
    session: AsyncSession,
    *,
    recipient: str | None = None,
    channel: str | None = None,
    status: str | None = None,
) -> list[Notification]:
    query = select(Notification)
    if recipient is not None:
        query = query.where(Notification.recipient == recipient)
    if channel is not None:
        query = query.where(Notification.channel == channel)
    if status is not None:
        query = query.where(Notification.status == status)
    result = await session.execute(query.order_by(Notification.created_at.desc()))
    return list(result.scalars().all())
