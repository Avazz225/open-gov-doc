import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notification_service import repository
from notification_service.settings import Settings

logger = logging.getLogger(__name__)


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
) -> Callable[[bytes], Awaitable[None]]:
    """Übersetzt `workflow.task.escalated` in Benachrichtigungen (Konzept 7.1, P6-S2):
    immer eine In-App-Notification (Empfänger = Lane-Name, sonst `"unassigned"` - keine
    Rollen-Auflösung ohne RBAC, siehe ADR 0020), zusätzlich eine E-Mail, falls die
    Prozessinstanz einen `escalation_email`-Wert mitgegeben hat (opakes Prozessdatum,
    Konvention wie `business_key` in workflow-service)."""

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
    """Benachrichtigung der Zielinstallation bei einer eingehenden föderierten
    Übergabe (7.4, P6-S9) - gleiches `notify_email`-Muster wie
    `escalation_email` bei `_handle_task_escalated`: ein optionales, opakes
    Prozessdatum aus der Payload der Absenderseite, keine feste
    Empfänger-Auflösung (workflow-service kennt kein RBAC-Konzept für
    Federation). Immer zusätzlich eine In-App-Notification, da es keinen
    Lane-Namen für einen frisch von außen gestarteten Prozess gibt."""
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
    """Löscherinnerung vor einer terminierten Aufbewahrungsfrist/
    Zwangslöschung (5.2a, P7-S1) - gleiches `notify_email`-Muster wie
    `escalation_email`/`notify_email` an anderer Stelle: ein optionales,
    opakes Datum aus der Payload des Producers (document-service), keine
    Empfänger-Auflösung über Rollen/Konten."""
    data = event.payload
    title = data.get("title", "?")
    full_deletion = data.get("full_deletion", False)
    action = "physisch zwangsgelöscht" if full_deletion else "in den Papierkorb verschoben"
    subject = f"Löschfrist erreicht bald: {title}"
    body = (
        f"Dokument {title!r} (id={event.subject}) wird am "
        f"{data.get('retention_until', '?')} {action}, sofern kein Legal Hold gesetzt wird."
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


async def _handle_folder_deletion_reminder(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    """Löscherinnerung für Ordner (5.2a, P7-S1b) - 1:1 dasselbe Muster wie
    `_handle_deletion_reminder` (P7-S1), nur für `folder-service`s
    `name`-Feld statt `title`."""
    data = event.payload
    name = data.get("name", "?")
    full_deletion = data.get("full_deletion", False)
    action = "physisch zwangsgelöscht" if full_deletion else "in den Papierkorb verschoben"
    subject = f"Löschfrist erreicht bald: {name}"
    body = (
        f"Ordner {name!r} (id={event.subject}) wird am "
        f"{data.get('retention_until', '?')} {action}, sofern kein Legal Hold gesetzt wird."
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


async def _handle_superuser_activated(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    event: Event,
) -> None:
    """Optionale Sicherheitsbenachrichtigung bei Break-Glass-Aktivierung (4.6,
    P6-S5) - E-Mail an eine fest hinterlegte Sicherheitsverantwortliche
    Adresse, kein Empfänger-Auflösungsmechanismus nötig (anders als bei
    `escalation_email`, das aus dem Event selbst kommt)."""
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
    """Sicherheitsbenachrichtigung bei Not-Shutdown-Aktivierung (4.8, P6-S6) -
    gleiches Muster wie `_handle_superuser_activated` (P6-S5)."""
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


async def publish_notification_result(
    publish_event: Callable[[str, str, dict], Awaitable[None]], notification
) -> None:
    if notification.status == "sent":
        await publish_event(
            "notification.sent",
            str(notification.id),
            {"channel": notification.channel, "recipient": notification.recipient},
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
        # Ein durable-Konsumentenname ist pro Stream eindeutig, nicht pro Subject -
        # `workflow.federation.inbound_received` (seit P6-S9) teilt sich den
        # "workflow"-Stream mit dem bereits bestehenden `workflow.task.escalated`
        # (P6-S2). Ein zweiter `subscribe()`-Aufruf mit demselben Durable-Namen
        # "notification-service" für ein anderes Filter-Subject auf demselben
        # Stream schlägt mit "consumer is already bound to a subscription" fehl -
        # daher ein eigener, zweiter Durable-Name nur für das neue Subject. Die
        # drei bereits bestehenden Subjects behalten ihren ursprünglichen
        # Durable-Namen (keine Neuzustellung ihres bisherigen Verlaufs).
        durable = (
            "notification-service-federation"
            if subject == "workflow.federation.inbound_received"
            else "notification-service"
        )
        try:
            await bus.subscribe(subject, handler, durable=durable)
        except SubjectNotFoundError:
            logger.warning(
                "Kein Stream für Subject %r gefunden - noch kein Producer gestartet? "
                "Wird bis zum nächsten Neustart nicht konsumiert.",
                subject,
            )
