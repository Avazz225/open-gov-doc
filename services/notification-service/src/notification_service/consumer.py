import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notification_service import repository
from notification_service.links import build_resource_link
from notification_service.rate_limiter import RecipientRateLimiter
from notification_service.settings import Settings
from notification_service.templates import (
    UnknownPlaceholderError,
    render_template,
    resolve_template,
)

logger = logging.getLogger(__name__)

# See start_consuming(): a durable name is unique per stream, not
# per subject. Every entry here shares its stream with at least
# one other subject and therefore needs its own durable name.
_SHARED_STREAM_DURABLE_OVERRIDES: dict[str, str] = {
    "workflow.federation.inbound_received": "notification-service-federation",
    "license.limit_exceeded": "notification-service-license-limit-exceeded",
    "license.expiring_soon": "notification-service-license-expiring-soon",
    "license.invalid": "notification-service-license-invalid",
    "document.lock.reminder": "notification-service-lock-reminder",
    # Third subject on the "workflow" stream (after `workflow.task.escalated`
    # and `workflow.federation.inbound_received`), Post-Roadmap Phase 35
    # Session 3 (ADR 0145) - same reasoning as those two above.
    "workflow.task_claim.abandoned": "notification-service-task-claim-abandoned",
    # P71-S1: third/fourth subjects on the "document" stream.
    "document.lock.force_released": "notification-service-lock-force-released",
    "document.force_unlock.failed": "notification-service-force-unlock-failed",
    # P71-S1: second/third subjects on the "permission" stream.
    "permission.approval.approved": "notification-service-approval-approved",
    "permission.approval.rejected": "notification-service-approval-rejected",
    # P71-S1: second subject on the new "storage" stream (first subject,
    # `storage.replication.failed_permanent`, needs no override).
    "storage.object_verify.mismatch": "notification-service-storage-verify-mismatch",
}


def make_handler(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
) -> Callable[[bytes], Awaitable[None]]:
    """Translates `workflow.task.escalated` into notifications (concept 7.1, P6-S2):
    always an in-app notification (recipient = lane name, otherwise `"unassigned"` - no
    role resolution without RBAC, see ADR 0020), plus an email if the
    process instance provided an `escalation_email` value (opaque process datum,
    convention like `business_key` in workflow-service)."""

    async def handle(payload: bytes) -> None:
        event = Event.from_bytes(payload)
        if event.event_type == "auth.superuser.activated":
            await _handle_superuser_activated(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "permission.maintenance_mode.activated":
            await _handle_maintenance_mode_activated(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "workflow.federation.inbound_received":
            await _handle_federation_inbound_received(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "document.deletion.reminder":
            await _handle_deletion_reminder(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "folder.deletion.reminder":
            await _handle_folder_deletion_reminder(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "document.lock.reminder":
            await _handle_lock_reminder(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "workflow.task_claim.abandoned":
            await _handle_task_claim_abandoned(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "virus_scan.completed":
            await _handle_virus_scan_completed(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "license.limit_exceeded":
            await _handle_license_limit_exceeded(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "license.expiring_soon":
            await _handle_license_expiring_soon(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "license.invalid":
            await _handle_license_invalid(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "document.lock.force_released":
            await _handle_lock_force_released(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "document.force_unlock.failed":
            await _handle_force_unlock_failed(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "permission.approval.approved":
            await _handle_approval_approved(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "permission.approval.rejected":
            await _handle_approval_rejected(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "storage.replication.failed_permanent":
            await _handle_storage_replication_failed(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "storage.object_verify.mismatch":
            await _handle_storage_verify_mismatch(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        await _handle_task_escalated(session_factory, settings, publish_event, rate_limiter, event)

    return handle


async def _render_or_fallback(
    session: AsyncSession,
    *,
    use_case: str,
    recipient: str,
    fallback_subject: str,
    fallback_body: str,
    **placeholders,
) -> tuple[str, str]:
    """Applies an admin-configured `EmailTemplate` (post-roadmap phase 30,
    ADR 0111) for `(use_case, recipient)` when one resolves - falls back to
    the caller's existing hardcoded subject/body verbatim otherwise (no row
    configured, or a configured template referencing an unknown
    placeholder), so an installation that has never configured Phase 30
    sees zero change in behavior."""
    template = await resolve_template(session, use_case=use_case, recipient=recipient)
    if template is None:
        return fallback_subject, fallback_body
    try:
        return (
            render_template(template.subject_template, **placeholders),
            render_template(template.body_template, **placeholders),
        )
    except UnknownPlaceholderError:
        logger.warning(
            "Konfigurierte E-Mail-Vorlage fuer use_case=%r verwendet einen "
            "unbekannten Platzhalter - falle auf Standardtext zurueck.",
            use_case,
        )
        return fallback_subject, fallback_body


async def _handle_task_escalated(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    data = event.payload
    task_name = data.get("task_name", "?")
    business_key = data.get("business_key")
    fallback_subject = f"SLA überschritten: {task_name}"
    fallback_body = (
        f"Prozessinstanz {event.subject} (business_key={business_key!r}) hat den "
        f"Task {task_name!r} nicht rechtzeitig abgeschlossen."
    )
    # Authenticated direct links (post-roadmap phase 29, ADR 0109): the
    # instance ID (`event.subject`) is already at hand here - appends a
    # clickable link to the reviewer-ui "Vorgang" detail view when an
    # installation has configured `reviewer_ui_public_base_url` (ADR 0105).
    link = build_resource_link(settings.reviewer_ui_public_base_url, "instance", event.subject)
    if link:
        fallback_body += f"\n\nVorgang öffnen: {link}"
    placeholders = {
        "task_name": task_name,
        "business_key": business_key,
        "instance_id": event.subject,
        "link": link or "",
    }

    async with session_factory() as session:
        recipient = data.get("lane") or "unassigned"
        subject, body = await _render_or_fallback(
            session,
            use_case="workflow.task.escalated",
            recipient=recipient,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            **placeholders,
        )
        in_app = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient=recipient,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, in_app)

        escalation_email = data.get("escalation_email")
        if escalation_email:
            subject, body = await _render_or_fallback(
                session,
                use_case="workflow.task.escalated",
                recipient=escalation_email,
                fallback_subject=fallback_subject,
                fallback_body=fallback_body,
                **placeholders,
            )
            email_notification = await repository.create_and_send(
                session,
                settings,
                channel="email",
                recipient=escalation_email,
                subject=subject,
                body=body,
                rate_limiter=rate_limiter,
            )
            await session.commit()
            await publish_notification_result(publish_event, email_notification)


async def _handle_federation_inbound_received(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
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
    fallback_subject = f"Neue föderierte Übergabe: {process_type}"
    fallback_body = (
        f"Installation {from_installation_id!r} hat einen Prozessschritt "
        f"({process_type!r}) übergeben - neue lokale Instanz {event.subject!r} gestartet."
    )
    placeholders = {
        "from_installation_id": from_installation_id,
        "process_type": process_type,
        "instance_id": event.subject,
    }

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="workflow.federation.inbound_received",
            recipient="unassigned",
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            **placeholders,
        )
        in_app = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient="unassigned",
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, in_app)

        notify_email = data.get("notify_email")
        if notify_email:
            subject, body = await _render_or_fallback(
                session,
                use_case="workflow.federation.inbound_received",
                recipient=notify_email,
                fallback_subject=fallback_subject,
                fallback_body=fallback_body,
                **placeholders,
            )
            email_notification = await repository.create_and_send(
                session,
                settings,
                channel="email",
                recipient=notify_email,
                subject=subject,
                body=body,
                rate_limiter=rate_limiter,
            )
            await session.commit()
            await publish_notification_result(publish_event, email_notification)


async def _handle_deletion_reminder(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
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
    fallback_subject = f"Löschfrist erreicht bald: {title}"
    retention_until = data.get("retention_until", "?")
    fallback_body = (
        f"Dokument {title!r} (id={event.subject}) wird am "
        f"{retention_until} {action}, sofern kein Legal Hold gesetzt wird."
    )
    # Authenticated direct links (post-roadmap phase 29, ADR 0109).
    link = build_resource_link(settings.user_ui_public_base_url, "document", event.subject)
    if link:
        fallback_body += f"\n\nDokument öffnen: {link}"
    placeholders = {
        "title": title,
        "document_id": event.subject,
        "retention_until": retention_until,
        "action": action,
        "link": link or "",
    }

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="document.deletion.reminder",
            recipient="unassigned",
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            **placeholders,
        )
        in_app = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient="unassigned",
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, in_app)

        notify_email = data.get("notify_email")
        if notify_email:
            subject, body = await _render_or_fallback(
                session,
                use_case="document.deletion.reminder",
                recipient=notify_email,
                fallback_subject=fallback_subject,
                fallback_body=fallback_body,
                **placeholders,
            )
            email_notification = await repository.create_and_send(
                session,
                settings,
                channel="email",
                recipient=notify_email,
                subject=subject,
                body=body,
                rate_limiter=rate_limiter,
            )
            await session.commit()
            await publish_notification_result(publish_event, email_notification)


async def _handle_folder_deletion_reminder(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Deletion reminder for folders (5.2a, P7-S1b) - 1:1 the same pattern as
    `_handle_deletion_reminder` (P7-S1), just for `folder-service`'s
    `name` field instead of `title`."""
    data = event.payload
    name = data.get("name", "?")
    full_deletion = data.get("full_deletion", False)
    action = "physisch zwangsgelöscht" if full_deletion else "in den Papierkorb verschoben"
    fallback_subject = f"Löschfrist erreicht bald: {name}"
    retention_until = data.get("retention_until", "?")
    fallback_body = (
        f"Ordner {name!r} (id={event.subject}) wird am "
        f"{retention_until} {action}, sofern kein Legal Hold gesetzt wird."
    )
    # Authenticated direct links (post-roadmap phase 29, ADR 0109).
    link = build_resource_link(settings.user_ui_public_base_url, "folder", event.subject)
    if link:
        fallback_body += f"\n\nOrdner öffnen: {link}"
    placeholders = {
        "name": name,
        "folder_id": event.subject,
        "retention_until": retention_until,
        "action": action,
        "link": link or "",
    }

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="folder.deletion.reminder",
            recipient="unassigned",
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            **placeholders,
        )
        in_app = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient="unassigned",
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, in_app)

        notify_email = data.get("notify_email")
        if notify_email:
            subject, body = await _render_or_fallback(
                session,
                use_case="folder.deletion.reminder",
                recipient=notify_email,
                fallback_subject=fallback_subject,
                fallback_body=fallback_body,
                **placeholders,
            )
            email_notification = await repository.create_and_send(
                session,
                settings,
                channel="email",
                recipient=notify_email,
                subject=subject,
                body=body,
                rate_limiter=rate_limiter,
            )
            await session.commit()
            await publish_notification_result(publish_event, email_notification)


async def _handle_lock_reminder(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Reminder for a document locked for a while (4.2, post-roadmap phase
    30 session 4, ADR 0111) - the first notification hook this lock
    feature has ever had (see ADR 0002, which deliberately kept the lock
    feature itself minimal). Unlike the deletion reminders, there is no
    separately configured notify-email address here - the lock's own
    `locked_by` (the person who most plausibly forgot about it) is both the
    in-app recipient and, when it resolves to a real email account, the
    channel a configured `EmailTemplate` targets."""
    data = event.payload
    title = data.get("title", "?")
    locked_by = data.get("locked_by", "?")
    fallback_subject = f"Dokument seit längerem gesperrt: {title}"
    fallback_body = (
        f"Dokument {title!r} (id={event.subject}) ist seit längerem von {locked_by!r} gesperrt."
    )
    # Authenticated direct links (post-roadmap phase 29, ADR 0109).
    link = build_resource_link(settings.user_ui_public_base_url, "document", event.subject)
    if link:
        fallback_body += f"\n\nDokument öffnen: {link}"

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="document.lock.reminder",
            recipient=locked_by,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            title=title,
            document_id=event.subject,
            locked_by=locked_by,
            link=link or "",
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient=locked_by,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_virus_scan_completed(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Uploader notification on a virus hit (Post-Roadmap Phase 44 Session
    4, ADR 0073's own documented gap: `virus_scan.completed` was already
    published unconditionally by `virus-scan-service`, but nothing ever
    consumed it - this is the first consumer at all). `virus_scan.
    completed` fires for BOTH `"clean"` and `"infected"` scans; only the
    latter is worth notifying anyone about, so a clean result is a
    silent no-op here, same "not every event needs a notification"
    pattern this module already has for its other consumed subjects."""
    data = event.payload
    if data.get("status") != "infected":
        return
    filename = data.get("filename", "?")
    document_id = data.get("document_id")
    # `created_by` is an optional Form field on virus-scan-service's own
    # `POST /scan` (`document_id`/`created_by` are both `Form(None)`) - a
    # present-but-`None` key means `.get(..., "?")`'s default never kicks
    # in, so this needs an explicit `or` fallback instead.
    created_by = data.get("created_by") or "?"
    threat_name = data.get("threat_name") or "unbekannt"
    fallback_subject = f"Virus gefunden: {filename}"
    fallback_body = (
        f"Beim Hochladen von {filename!r} wurde ein Virus gefunden ({threat_name}) - "
        "die Datei wurde in Quarantäne verschoben."
    )
    link = (
        build_resource_link(settings.user_ui_public_base_url, "document", document_id)
        if document_id
        else None
    )
    if link:
        fallback_body += f"\n\nDokument öffnen: {link}"

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="virus_scan.completed",
            recipient=created_by,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            filename=filename,
            document_id=document_id or "",
            threat_name=threat_name,
            link=link or "",
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient=created_by,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_task_claim_abandoned(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Claim-abandonment notice (14.2/8, Post-Roadmap Phase 35 Session 3,
    ADR 0145) - the notification half of the feature ADR 0121 scoped out
    ("no notifications"). Same shape as `_handle_lock_reminder` above (the
    closest existing precedent: a single, in-app-only nudge to the person
    who most plausibly forgot about the thing they're holding) - `event.
    subject` is the process instance ID here, not the claim's own resource,
    since `TaskClaim` has no direct link/URL scheme of its own; the
    reviewer-ui "Vorgang" detail view is the closest existing thing to
    point at."""
    data = event.payload
    task_name = data.get("task_name", "?")
    principal_id = data.get("principal_id", "?")
    business_key = data.get("business_key")
    fallback_subject = f"Beanspruchter Task seit längerem offen: {task_name}"
    fallback_body = (
        f"Der Task {task_name!r} (Vorgang {event.subject}, business_key={business_key!r}) ist "
        f"seit längerem von {principal_id!r} beansprucht, aber nicht abgeschlossen."
    )
    # Authenticated direct links (post-roadmap phase 29, ADR 0109/0110) -
    # same "instance" resource type `_handle_task_escalated` already uses.
    link = build_resource_link(settings.reviewer_ui_public_base_url, "instance", event.subject)
    if link:
        fallback_body += f"\n\nVorgang öffnen: {link}"

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="workflow.task_claim.abandoned",
            recipient=principal_id,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            task_name=task_name,
            instance_id=event.subject,
            business_key=business_key,
            principal_id=principal_id,
            link=link or "",
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient=principal_id,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_superuser_activated(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Optional security notification on break-glass activation (4.6,
    P6-S5) - email to a fixed configured security officer
    address, no recipient resolution mechanism needed (unlike
    `escalation_email`, which comes from the event itself)."""
    expires_at = event.payload.get("expires_at", "?")
    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="auth.superuser.activated",
            recipient=settings.security_officer_email,
            fallback_subject="Superuser Break-Glass aktiviert",
            fallback_body=f"Der Superuser-Zugang wurde aktiviert und läuft ab: {expires_at}.",
            expires_at=expires_at,
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.security_officer_email,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_maintenance_mode_activated(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Security notification on emergency-shutdown activation (4.8, P6-S6) -
    same pattern as `_handle_superuser_activated` (P6-S5)."""
    triggered_by = event.payload.get("triggered_by", "?")
    reason = event.payload.get("reason") or "kein Grund angegeben"
    fallback_body = f"Der Wartungsmodus wurde von {triggered_by!r} ausgelöst. Grund: {reason}."
    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="permission.maintenance_mode.activated",
            recipient=settings.security_officer_email,
            fallback_subject="Systemweite Notfallsperre ausgelöst",
            fallback_body=fallback_body,
            triggered_by=triggered_by,
            reason=reason,
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.security_officer_email,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_license_limit_exceeded(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """License status notification (9.2, since P9-S1) - same pattern as
    `_handle_maintenance_mode_activated`, published only once per edge
    change (see `license_service.poll_loop`)."""
    dimension = event.payload.get("dimension", "?")
    current = event.payload.get("current")
    limit = event.payload.get("limit")
    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="license.limit_exceeded",
            recipient=settings.license_admin_email,
            fallback_subject="Lizenz-Nutzungsgrenze überschritten",
            fallback_body=f"Dimension {dimension!r} liegt bei {current}, Lizenzgrenze ist {limit}.",
            dimension=dimension,
            current=current,
            limit=limit,
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.license_admin_email,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_license_expiring_soon(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    days_remaining = event.payload.get("days_remaining", "?")
    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="license.expiring_soon",
            recipient=settings.license_admin_email,
            fallback_subject="Lizenz läuft bald ab",
            fallback_body=f"Die installierte Lizenz läuft in {days_remaining} Tagen ab.",
            days_remaining=days_remaining,
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.license_admin_email,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_license_invalid(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    reason = event.payload.get("reason") or "kein Grund angegeben"
    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="license.invalid",
            recipient=settings.license_admin_email,
            fallback_subject="Lizenz ungültig",
            fallback_body=f"Die installierte Lizenz ist ungültig: {reason}.",
            reason=reason,
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.license_admin_email,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_lock_force_released(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Force-unlock execution feedback, success case (4.2/4.3, P71-S1) -
    `document-service` has published this unconditionally since P6-S4, just
    never consumed here before (ADR 0022's own "Consequences" named this
    gap). Recipient is the ORIGINAL lock holder (`original_locked_by`), the
    person most plausibly affected by having their lock pulled - not
    `released_by` (the approver who requested the force-unlock), same
    "notify the affected party" reasoning as `_handle_lock_reminder`
    above."""
    data = event.payload
    original_locked_by = data.get("original_locked_by", "?")
    released_by = data.get("released_by", "?")
    reason = data.get("reason")
    fallback_subject = "Dokumentsperre zwangsweise aufgehoben"
    fallback_body = (
        f"Ihre Sperre für Dokument (id={event.subject}) wurde von {released_by!r} zwangsweise "
        f"aufgehoben."
    )
    if reason:
        fallback_body += f" Grund: {reason}"
    link = build_resource_link(settings.user_ui_public_base_url, "document", event.subject)
    if link:
        fallback_body += f"\n\nDokument öffnen: {link}"

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="document.lock.force_released",
            recipient=original_locked_by,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            document_id=event.subject,
            original_locked_by=original_locked_by,
            released_by=released_by,
            reason=reason or "",
            link=link or "",
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient=original_locked_by,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_force_unlock_failed(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Force-unlock execution feedback, failure case (4.2/4.3, P71-S1) -
    the counterpart to `_handle_lock_force_released` above, closing the
    exact gap ADR 0022's "Consequences" section named ("no execution
    feedback channel... a failed force-unlock is only logged locally").
    Recipient is `released_by` (the approver whose already-approved request
    could not actually be executed, e.g. because the lock had meanwhile
    been released some other way) - they are the one who needs to know
    their action did not take effect, not the original lock holder (who
    was never affected, since nothing actually changed)."""
    data = event.payload
    released_by = data.get("released_by", "?")
    reason = data.get("reason", "?")
    fallback_subject = "Zwangsweise Sperrenfreigabe fehlgeschlagen"
    fallback_body = (
        f"Die genehmigte zwangsweise Freigabe der Sperre für Dokument (id={event.subject}) "
        f"konnte nicht ausgeführt werden: {reason}"
    )
    link = build_resource_link(settings.user_ui_public_base_url, "document", event.subject)
    if link:
        fallback_body += f"\n\nDokument öffnen: {link}"

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="document.force_unlock.failed",
            recipient=released_by,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            document_id=event.subject,
            released_by=released_by,
            reason=reason,
            link=link or "",
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient=released_by,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_approval_approved(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Four-eyes approval lifecycle feedback, approved case (4.3, P71-S1) -
    the initiator of an approval request previously had no way to learn
    its outcome except by polling `GET /approval-requests/{id}` at
    permission-service directly. Recipient is `initiated_by` - the person
    who requested the action, now told it was approved (execution itself
    follows separately, asynchronously, via whichever service's own
    consumer owns that action type)."""
    data = event.payload
    initiated_by = data.get("initiated_by", "?")
    action_type = data.get("action_type", "?")
    approved_by = data.get("approved_by", "?")
    # `permission-service`'s own `publish_event` has no `subject` parameter
    # at all (unlike document-service's) - `request_id` lives in the
    # payload, not `event.subject`.
    request_id = data.get("request_id", "?")
    fallback_subject = "Genehmigungsanfrage bewilligt"
    fallback_body = f"Ihre Genehmigungsanfrage ({action_type}) wurde von {approved_by!r} bewilligt."

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="permission.approval.approved",
            recipient=initiated_by,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            action_type=action_type,
            approved_by=approved_by,
            request_id=request_id,
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient=initiated_by,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_approval_rejected(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Four-eyes approval lifecycle feedback, rejected case (4.3, P71-S1) -
    counterpart to `_handle_approval_approved` above."""
    data = event.payload
    initiated_by = data.get("initiated_by", "?")
    action_type = data.get("action_type", "?")
    rejected_by = data.get("rejected_by", "?")
    reason = data.get("reason")
    request_id = data.get("request_id", "?")
    fallback_subject = "Genehmigungsanfrage abgelehnt"
    fallback_body = f"Ihre Genehmigungsanfrage ({action_type}) wurde von {rejected_by!r} abgelehnt."
    if reason:
        fallback_body += f" Grund: {reason}"

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="permission.approval.rejected",
            recipient=initiated_by,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            action_type=action_type,
            rejected_by=rejected_by,
            reason=reason or "",
            request_id=request_id,
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient=initiated_by,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_storage_replication_failed(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Permanently-failed replication (3.6, P71-S1) - `storage-service`
    now publishes this instead of only logging it (an already-real
    operational pain point: 30,410 orphaned rows once needed manual SQL
    cleanup, see docs/services/storage-service.md). No principal identity
    in the payload at all (an object key/backend id, not a person) - fixed
    admin address, same pattern as `security_officer_email`/
    `license_admin_email`."""
    data = event.payload
    object_key = data.get("object_key", event.subject)
    backend_id = data.get("backend_id", "?")
    attempts = data.get("attempts", "?")
    error = data.get("error", "?")
    fallback_subject = "Replikation dauerhaft fehlgeschlagen"
    fallback_body = (
        f"Die Replikation von Objekt {object_key!r} auf Backend {backend_id!r} ist nach "
        f"{attempts} Versuchen dauerhaft fehlgeschlagen: {error}"
    )

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="storage.replication.failed_permanent",
            recipient=settings.storage_admin_email,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            object_key=object_key,
            backend_id=backend_id,
            attempts=attempts,
            error=error,
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.storage_admin_email,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def _handle_storage_verify_mismatch(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Fixity mismatch (3.6 "regular fixity check", P71-S1) - a data-
    integrity event, previously only visible via the storage-guard UI.
    Counterpart to `_handle_storage_replication_failed` above."""
    data = event.payload
    object_key = data.get("object_key", event.subject)
    backend_ids = data.get("backend_ids", [])
    fallback_subject = "Prüfsummen-Abweichung festgestellt"
    fallback_body = (
        f"Bei der regelmäßigen Fixity-Prüfung von Objekt {object_key!r} wurde auf "
        f"Backend(s) {', '.join(backend_ids) or '?'} eine Prüfsummen-Abweichung festgestellt."
    )

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="storage.object_verify.mismatch",
            recipient=settings.storage_admin_email,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            object_key=object_key,
            backend_ids=", ".join(backend_ids),
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.storage_admin_email,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


async def publish_notification_result(
    publish_event: Callable[[str, str, dict], Awaitable[None]], notification
) -> None:
    # Rate-limited skip (`create_and_send` returned None) - nothing to report.
    if notification is None:
        return
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
    rate_limiter: RecipientRateLimiter,
) -> None:
    handler = make_handler(session_factory, settings, publish_event, rate_limiter)
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
