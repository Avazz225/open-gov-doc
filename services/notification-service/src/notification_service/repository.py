from datetime import UTC, datetime, timedelta

from dms_retry import compute_backoff_seconds
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service import delivery
from notification_service.models import Notification
from notification_service.settings import Settings


class NotFoundError(Exception):
    pass


async def attempt_delivery(
    session: AsyncSession, settings: Settings, notification: Notification, *, max_attempts: int
) -> None:
    """Ein einzelner Zustellversuch (Konzept 7.1, P6-S2) - genutzt sowohl beim
    ersten, synchronen Versuch (`create_and_send`) als auch bei jedem späteren
    Wiederholungsversuch (`_notification_retry_poll_loop`/manueller Neustart,
    Post-Roadmap Phase 20 Session 3, ADR 0079). Bei Erfolg `status="sent"`; bei
    einem `DeliveryError` unterhalb von `max_attempts` bleibt `status="failed"`
    (retry-fähig) mit einem per `compute_backoff_seconds` gesetzten
    `next_retry_at`, erst bei Erschöpfung wechselt `status` auf das echte
    Terminalstatus `failed_permanent`."""
    try:
        if notification.channel == "email":
            await delivery.send_email(
                settings, notification.recipient, notification.subject, notification.body
            )
        elif notification.channel == "webhook":
            await delivery.send_webhook(
                notification.recipient, notification.subject, notification.body
            )
        # "in_app" wird ausschließlich persistiert, keine weitere Zustellung.
        notification.status = "sent"
        notification.sent_at = datetime.now(UTC)
        notification.error = None
        notification.next_retry_at = None
    except delivery.DeliveryError as exc:
        notification.attempts += 1
        notification.error = str(exc)
        if notification.attempts >= max_attempts:
            notification.status = "failed_permanent"
            notification.next_retry_at = None
        else:
            notification.status = "failed"
            delay = compute_backoff_seconds(notification.attempts - 1)
            notification.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
    await session.flush()


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
    das Ergebnis (`status`/`error`/`sent_at`/`attempts`/`next_retry_at`) landet direkt
    am selben Datensatz. Ein Fehlschlag ist seit Post-Roadmap Phase 20 Session 3 (ADR
    0079) nicht mehr sofort terminal, siehe `attempt_delivery`."""
    now = datetime.now(UTC)
    notification = Notification(
        channel=channel,
        recipient=recipient,
        subject=subject,
        body=body,
        status="failed",
        error=None,
        attempts=0,
        next_retry_at=None,
        created_at=now,
        sent_at=None,
    )
    session.add(notification)
    await session.flush()

    await attempt_delivery(
        session, settings, notification, max_attempts=settings.max_notification_attempts
    )
    return notification


async def list_due_for_retry(session: AsyncSession) -> list[Notification]:
    """Retry-fähige Notifications, deren Backoff-Fenster bereits abgelaufen ist
    (Post-Roadmap Phase 20 Session 3, ADR 0079) - abgearbeitet vom neuen
    `_notification_retry_poll_loop`."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(Notification).where(
            Notification.status == "failed",
            or_(Notification.next_retry_at.is_(None), Notification.next_retry_at <= now),
        )
    )
    return list(result.scalars().all())


async def retry_now(
    session: AsyncSession, settings: Settings, notification: Notification, *, max_attempts: int
) -> None:
    """Manueller Neustart einer `failed_permanent`-Notification (`POST
    /notifications/{id}/retry`, Post-Roadmap Phase 20 Session 3) - setzt
    `attempts` zurück und unternimmt SOFORT einen neuen synchronen
    Zustellversuch (anders als archival-service's `reset_for_retry`, das nur auf
    `pending` zurücksetzt und den nächsten Poll-Tick abwartet): eine Notification
    ist ein einzelner, leichtgewichtiger Zustellschritt, kein mehrphasiger
    Prozess - ein Admin, der auf "erneut versuchen" klickt, erwartet ein
    sofortiges Ergebnis, nicht ein Warten auf den nächsten Poll-Tick."""
    notification.attempts = 0
    notification.error = None
    notification.next_retry_at = None
    await attempt_delivery(session, settings, notification, max_attempts=max_attempts)


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
