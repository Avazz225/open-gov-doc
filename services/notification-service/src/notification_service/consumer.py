import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notification_service import repository
from notification_service.links import build_resource_link
from notification_service.settings import Settings

logger = logging.getLogger(__name__)

# See start_consuming(): a durable name is unique per stream, not
# per subject. Every entry here shares its stream with at least
# one other subject and therefore needs its own durable name.
_SHARED_STREAM_DURABLE_OVERRIDES: dict[str, str] = {
    "workflow.federation.inbound_received": "notification-service-federation",
    "license.limit_exceeded": "notification-service-license-limit-exceeded",
    "license.expiring_soon": "notification-service-license-expiring-soon",
    "license.invalid": "notification-service-license-invalid",
}


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
) -> Callable[[bytes], Awaitable[None]]:
    """Translates `workflow.task.escalated` into notifications (concept 7.1, P6-S2):
    always an in-app notification (recipient = lane name, otherwise `"unassigned"` - no
    role resolution without RBAC, see ADR 0020), plus an email if the
    process instance provided an `escalation_email` value (opaque process datum,
    convention like `business_key` in workflow-service)."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.event_type == "auth.superuser.activated":
            await _handle_superuser_activated(session_factory, settings, publish_event, event)
            return
        if event.event_type == "permission.maintenance_mode.activated":
            await _handle_maintenance_mode_activated(
                session_factory, settings, publish_event, event
            )
            return
        if event.event_type == "workflow.federation.inbound_received":
            await _handle_federation_inbound_received(
                session_factory, settings, publish_event, event
            )
            return
        if event.event_type == "document.deletion.reminder":
            await _handle_deletion_reminder(session_factory, settings, publish_event, event)
            return
        if event.event_type == "folder.deletion.reminder":
            await _handle_folder_deletion_reminder(session_factory, settings, publish_event, event)
            return
        if event.event_type == "license.limit_exceeded":
            await _handle_license_limit_exceeded(session_factory, settings, publish_event, event)
            return
        if event.event_type == "license.expiring_soon":
            await _handle_license_expiring_soon(session_factory, settings, publish_event, event)
            return
        if event.event_type == "license.invalid":
            await _handle_license_invalid(session_factory, settings, publish_event, event)
            return
        await _handle_task_escalated(session_factory, settings, publish_event, event)

    return handle


async def _handle_task_escalated(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    data = event.payload
    task_name = data.get("task_name", "?")
    business_key = data.get("business_key")
    subject = f"SLA überschritten: {task_name}"
    body = (
        f"Prozessinstanz {event.subject} (business_key={business_key!r}) hat den "
        f"Task {task_name!r} nicht rechtzeitig abgeschlossen."
    )
    # Authenticated direct links (post-roadmap phase 29, ADR 0109): the
    # instance ID (`event.subject`) is already at hand here - appends a
    # clickable link to the reviewer-ui "Vorgang" detail view when an
    # installation has configured `reviewer_ui_public_base_url` (ADR 0105).
    link = build_resource_link(settings.reviewer_ui_public_base_url, "instance", event.subject)
    if link:
        body += f"\n\nVorgang öffnen: {link}"

    async with session_factory() as session:
        recipient = data.get("lane") or "unassigned"
        in_app = await repository.create_and_send(
            session, settings, channel="in_app", recipient=recipient, subject=subject, body=body
        )
        await session.commit()
        await publish_notification_result(publish_event, in_app)

        escalation_email = data.get("escalation_email")
        if escalation_email:
            email_notification = await repository.create_and_send(
                session,
                settings,
                channel="email",
                recipient=escalation_email,
                subject=subject,
                body=body,
            )
            await session.commit()
            await publish_notification_result(publish_event, email_notification)


async def _handle_federation_inbound_received(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    """Notifies the target installation of an incoming federated
    handoff (7.4, P6-S9) - same `notify_email` pattern as
    `escalation_email` in `_handle_task_escalated`: an optional, opaque
    process datum from the sender side's payload, no fixed
    recipient resolution (workflow-service has no RBAC concept for
    federation). Always additionally an in-app notification, since there
    is no lane name for a process freshly started from outside."""
    data = event.payload
    from_installation_id = data.get("from_installation_id", "?")
    process_type = data.get("process_type", "?")
    subject = f"Neue föderierte Übergabe: {process_type}"
    body = (
        f"Installation {from_installation_id!r} hat einen Prozessschritt "
        f"({process_type!r}) übergeben - neue lokale Instanz {event.subject!r} gestartet."
    )

    async with session_factory() as session:
        in_app = await repository.create_and_send(
            session, settings, channel="in_app", recipient="unassigned", subject=subject, body=body
        )
        await session.commit()
        await publish_notification_result(publish_event, in_app)

        notify_email = data.get("notify_email")
        if notify_email:
            email_notification = await repository.create_and_send(
                session,
                settings,
                channel="email",
                recipient=notify_email,
                subject=subject,
                body=body,
            )
            await session.commit()
            await publish_notification_result(publish_event, email_notification)


async def _handle_deletion_reminder(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    """Deletion reminder before a scheduled retention period/
    forced deletion (5.2a, P7-S1) - same `notify_email` pattern as
    `escalation_email`/`notify_email` elsewhere: an optional,
    opaque date from the producer's payload (document-service), no
    recipient resolution via roles/accounts."""
    data = event.payload
    title = data.get("title", "?")
    full_deletion = data.get("full_deletion", False)
    action = "physisch zwangsgelöscht" if full_deletion else "in den Papierkorb verschoben"
    subject = f"Löschfrist erreicht bald: {title}"
    body = (
        f"Dokument {title!r} (id={event.subject}) wird am "
        f"{data.get('retention_until', '?')} {action}, sofern kein Legal Hold gesetzt wird."
    )
    # Authenticated direct links (post-roadmap phase 29, ADR 0109).
    link = build_resource_link(settings.user_ui_public_base_url, "document", event.subject)
    if link:
        body += f"\n\nDokument öffnen: {link}"

    async with session_factory() as session:
        in_app = await repository.create_and_send(
            session, settings, channel="in_app", recipient="unassigned", subject=subject, body=body
        )
        await session.commit()
        await publish_notification_result(publish_event, in_app)

        notify_email = data.get("notify_email")
        if notify_email:
            email_notification = await repository.create_and_send(
                session,
                settings,
                channel="email",
                recipient=notify_email,
                subject=subject,
                body=body,
            )
            await session.commit()
            await publish_notification_result(publish_event, email_notification)


async def _handle_folder_deletion_reminder(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    """Deletion reminder for folders (5.2a, P7-S1b) - 1:1 the same pattern as
    `_handle_deletion_reminder` (P7-S1), just for `folder-service`'s
    `name` field instead of `title`."""
    data = event.payload
    name = data.get("name", "?")
    full_deletion = data.get("full_deletion", False)
    action = "physisch zwangsgelöscht" if full_deletion else "in den Papierkorb verschoben"
    subject = f"Löschfrist erreicht bald: {name}"
    body = (
        f"Ordner {name!r} (id={event.subject}) wird am "
        f"{data.get('retention_until', '?')} {action}, sofern kein Legal Hold gesetzt wird."
    )
    # Authenticated direct links (post-roadmap phase 29, ADR 0109).
    link = build_resource_link(settings.user_ui_public_base_url, "folder", event.subject)
    if link:
        body += f"\n\nOrdner öffnen: {link}"

    async with session_factory() as session:
        in_app = await repository.create_and_send(
            session, settings, channel="in_app", recipient="unassigned", subject=subject, body=body
        )
        await session.commit()
        await publish_notification_result(publish_event, in_app)

        notify_email = data.get("notify_email")
        if notify_email:
            email_notification = await repository.create_and_send(
                session,
                settings,
                channel="email",
                recipient=notify_email,
                subject=subject,
                body=body,
            )
            await session.commit()
            await publish_notification_result(publish_event, email_notification)


async def _handle_superuser_activated(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    """Optional security notification on break-glass activation (4.6,
    P6-S5) - email to a fixed configured security officer
    address, no recipient resolution mechanism needed (unlike
    `escalation_email`, which comes from the event itself)."""
    expires_at = event.payload.get("expires_at", "?")
    async with session_factory() as session:
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.security_officer_email,
            subject="Superuser Break-Glass aktiviert",
            body=f"Der Superuser-Zugang wurde aktiviert und läuft ab: {expires_at}.",
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_maintenance_mode_activated(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    """Security notification on emergency-shutdown activation (4.8, P6-S6) -
    same pattern as `_handle_superuser_activated` (P6-S5)."""
    triggered_by = event.payload.get("triggered_by", "?")
    reason = event.payload.get("reason") or "kein Grund angegeben"
    async with session_factory() as session:
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.security_officer_email,
            subject="Systemweite Notfallsperre ausgelöst",
            body=f"Der Wartungsmodus wurde von {triggered_by!r} ausgelöst. Grund: {reason}.",
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_license_limit_exceeded(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    """License status notification (9.2, since P9-S1) - same pattern as
    `_handle_maintenance_mode_activated`, published only once per edge
    change (see `license_service.poll_loop`)."""
    dimension = event.payload.get("dimension", "?")
    current = event.payload.get("current")
    limit = event.payload.get("limit")
    async with session_factory() as session:
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.license_admin_email,
            subject="Lizenz-Nutzungsgrenze überschritten",
            body=f"Dimension {dimension!r} liegt bei {current}, Lizenzgrenze ist {limit}.",
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_license_expiring_soon(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    days_remaining = event.payload.get("days_remaining", "?")
    async with session_factory() as session:
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.license_admin_email,
            subject="Lizenz läuft bald ab",
            body=f"Die installierte Lizenz läuft in {days_remaining} Tagen ab.",
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_license_invalid(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    reason = event.payload.get("reason") or "kein Grund angegeben"
    async with session_factory() as session:
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.license_admin_email,
            subject="Lizenz ungültig",
            body=f"Die installierte Lizenz ist ungültig: {reason}.",
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def publish_notification_result(
    publish_event: Callable[[str, str, dict], Awaitable[None]], notification
) -> None:
    # System-triggered delivery attempt, not a human actor
    # (see P7-S2 convention "system:<component>").
    if notification.status == "sent":
        await publish_event(
            "notification.sent",
            str(notification.id),
            {"channel": notification.channel, "recipient": notification.recipient},
            actor="system:notification-service",
        )
    else:
        await publish_event(
            "notification.failed",
            str(notification.id),
            {
                "channel": notification.channel,
                "recipient": notification.recipient,
                "error": notification.error,
            },
            actor="system:notification-service",
        )


async def start_consuming(
    bus: NatsEventBusClient,
    subjects: list[str],
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
) -> None:
    handler = make_handler(session_factory, settings, publish_event)
    for subject in subjects:
        # A durable consumer name is unique per stream, not per subject -
        # `workflow.federation.inbound_received` (since P6-S9) shares the
        # "workflow" stream with the already existing `workflow.task.escalated`
        # (P6-S2). A second `subscribe()` call with the same durable name
        # "notification-service" for a different filter subject on the same
        # stream fails with "consumer is already bound to a subscription" -
        # hence a dedicated durable name for each additional subject that
        # shares a stream with sibling subjects. The three original subjects
        # keep their durable name (no redelivery of their existing
        # history). The three "license.>" subjects (P9-S1) all share the
        # new "license" stream with each other - so each gets its own
        # durable name instead of just one default+two exceptions (symmetry/
        # readability, no technical necessity for the first of the three).
        durable = _SHARED_STREAM_DURABLE_OVERRIDES.get(subject, "notification-service")
        try:
            await bus.subscribe(subject, handler, durable=durable)
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
