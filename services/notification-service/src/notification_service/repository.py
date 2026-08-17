from datetime import UTC, datetime, timedelta

from dms_retry import compute_backoff_seconds
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from notification_service import delivery
from notification_service.models import EmailTemplate, Notification
from notification_service.settings import Settings


class NotFoundError(Exception):
    pass


async def attempt_delivery(
    session: AsyncSession, settings: Settings, notification: Notification, *, max_attempts: int
) -> None:
    """A single delivery attempt (Concept 7.1, P6-S2) - used both for the first,
    synchronous attempt (`create_and_send`) and for every later retry attempt
    (`_notification_retry_poll_loop`/manual restart, Post-Roadmap Phase 20
    Session 3, ADR 0079). On success `status="sent"`; on a `DeliveryError` below
    `max_attempts`, `status="failed"` remains (retry-capable) with a
    `next_retry_at` set via `compute_backoff_seconds`; only once exhausted does
    `status` switch to the real terminal status `failed_permanent`."""
    try:
        if notification.channel == "email":
            await delivery.send_email(
                settings, notification.recipient, notification.subject, notification.body
            )
        elif notification.channel == "webhook":
            await delivery.send_webhook(
                notification.recipient, notification.subject, notification.body
            )
        # "in_app" is only persisted, no further delivery.
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
    """Persists first, then attempts synchronous delivery (Concept 7.1, P6-S2) -
    the result (`status`/`error`/`sent_at`/`attempts`/`next_retry_at`) is written
    directly to the same record. Since Post-Roadmap Phase 20 Session 3 (ADR
    0079), a failure is no longer immediately terminal, see `attempt_delivery`."""
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
    """Retry-capable notifications whose backoff window has already elapsed
    (Post-Roadmap Phase 20 Session 3, ADR 0079) - worked through by the new
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
    """Manual restart of a `failed_permanent` notification (`POST
    /notifications/{id}/retry`, Post-Roadmap Phase 20 Session 3) - resets
    `attempts` and IMMEDIATELY makes a new synchronous delivery attempt (unlike
    archival-service's `reset_for_retry`, which only resets to `pending` and
    waits for the next poll tick): a notification is a single, lightweight
    delivery step, not a multi-phase process - an admin who clicks "retry"
    expects an immediate result, not a wait for the next poll tick."""
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


# --- Configurable email templates (post-roadmap phase 30, ADR 0111) -------
# Same keyed-discriminator + "no row = fallback" shape as permission-
# service's ApprovalActionConfig - see models.py's EmailTemplate docstring.


async def list_email_templates(
    session: AsyncSession, *, use_case: str | None = None
) -> list[EmailTemplate]:
    """Deliberately returns ONLY explicitly configured rows (same convention
    as `ApprovalActionConfig`, ADR 0089) - not a fixed catalog of every
    `use_case` that could theoretically exist (see `main.py`'s separate
    `GET /email-template-use-cases` for that catalog)."""
    query = select(EmailTemplate)
    if use_case is not None:
        query = query.where(EmailTemplate.use_case == use_case)
    result = await session.execute(
        query.order_by(EmailTemplate.use_case, EmailTemplate.recipient_domain_pattern)
    )
    return list(result.scalars().all())


async def get_email_template(session: AsyncSession, template_id: int) -> EmailTemplate:
    template = await session.get(EmailTemplate, template_id)
    if template is None:
        raise NotFoundError(f"email_template_id {template_id!r} unbekannt")
    return template


async def upsert_email_template(
    session: AsyncSession,
    *,
    use_case: str,
    recipient_domain_pattern: str | None,
    subject_template: str,
    body_template: str,
) -> EmailTemplate:
    result = await session.execute(
        select(EmailTemplate).where(
            EmailTemplate.use_case == use_case,
            EmailTemplate.recipient_domain_pattern == recipient_domain_pattern
            if recipient_domain_pattern is not None
            else EmailTemplate.recipient_domain_pattern.is_(None),
        )
    )
    existing = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if existing is not None:
        existing.subject_template = subject_template
        existing.body_template = body_template
        existing.updated_at = now
        await session.flush()
        return existing

    template = EmailTemplate(
        use_case=use_case,
        recipient_domain_pattern=recipient_domain_pattern,
        subject_template=subject_template,
        body_template=body_template,
        updated_at=now,
    )
    session.add(template)
    await session.flush()
    return template


async def delete_email_template(session: AsyncSession, template_id: int) -> None:
    template = await get_email_template(session, template_id)
    await session.delete(template)
    await session.flush()
