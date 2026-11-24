import logging
from collections.abc import Awaitable, Callable

from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from notification_service import repository
from notification_service.auth_client import AuthServiceClient
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
    # P93-S2 (ADR 0249 Part A c): administrative instance changes.
    "workflow.instance.modified": "notification-service-instance-modified",
    # P71-S1: third/fourth subjects on the "document" stream.
    "document.lock.force_released": "notification-service-lock-force-released",
    "document.force_unlock.failed": "notification-service-force-unlock-failed",
    # P71-S1: second/third subjects on the "permission" stream.
    "permission.approval.approved": "notification-service-approval-approved",
    "permission.approval.rejected": "notification-service-approval-rejected",
    # P90-S3 (ADR 0247): fourth subject on the "permission" stream.
    "permission.approval.requested": "notification-service-approval-requested",
    # P90-S4 (ADR 0256): second subject on the "favorite" stream.
    "favorite.watch.triggered": "notification-service-watch-triggered",
    # P71-S1: second subject on the new "storage" stream (first subject,
    # `storage.replication.failed_permanent`, needs no override).
    "storage.object_verify.mismatch": "notification-service-storage-verify-mismatch",
    # P87-S3 (ADR 0241): the replica alarms share the "registry" stream with
    # registry.instance.* (which only audit-service consumes) and with each
    # other, so each gets its own durable name.
    "registry.replicas.under": "notification-service-replicas-under",
    "registry.replicas.over": "notification-service-replicas-over",
    "registry.replicas.restored": "notification-service-replicas-restored",
    # P88-S5 (ADR 0245): sensor alerts on the "monitoring" stream, which
    # audit-service also consumes - own durable names.
    "monitoring.alert.raised": "notification-service-alert-raised",
    "monitoring.alert.resolved": "notification-service-alert-resolved",
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
        if event.event_type == "permission.approval.requested":
            await _handle_approval_requested(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "favorite.watch.triggered":
            await _handle_watch_triggered(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type == "favorite.reminder.due":
            await _handle_reminder_due(
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
        if event.event_type == "workflow.instance.modified":
            await _handle_instance_modified(
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
        if event.event_type in _REPLICA_FALLBACKS:
            await _handle_replica_alarm(
                session_factory, settings, publish_event, rate_limiter, event
            )
            return
        if event.event_type in _SENSOR_ALERT_FALLBACKS:
            await _handle_sensor_alert(
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
        # A bound lane (P93-S1, ADR 0249) names its members: each gets the
        # in-app notice. An unbound lane keeps the previous behaviour - one
        # notice addressed to the lane name.
        members = [m for m in (data.get("lane_members") or []) if isinstance(m, str)]
        recipients = members or [data.get("lane") or "unassigned"]
        for recipient in recipients:
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


_OBJECT_LABELS = {"document": "Dokument", "folder": "Ordner", "case": "Umlaufmappe"}


# What happened, in words (P90-S4). Keys are favorite-service's change codes.
_WATCH_CHANGES = {
    "new_version": "neue Version",
    "metadata_updated": "Metadaten geändert",
    "moved": "verschoben",
    "trashed": "in den Papierkorb gelegt",
    "restored": "wiederhergestellt",
    "deleted": "endgültig gelöscht",
    "folder_updated": "umbenannt oder geändert",
    "document_added": "neues Dokument",
    "document_moved_in": "Dokument hierher verschoben",
}


async def _handle_watch_triggered(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """A watched document or folder changed (P90-S4, ADR 0256): in-app to the
    watcher and a mail when the account has an address - the same delivery as
    follow-up reminders. favorite-service already left out the person who
    made the change and checked the watcher can still read the object. The
    link opens what changed: the added document for "neues Dokument", the
    watched object otherwise (nothing for a deleted one)."""
    data = event.payload
    user = data.get("user_id", "?")
    object_type = data.get("object_type", "document")
    change_code = data.get("change", "")
    change = _WATCH_CHANGES.get(change_code, change_code)
    label = _OBJECT_LABELS.get(object_type, object_type)
    title = data.get("title") or ""
    subject_type = data.get("subject_type") or object_type
    subject_id = data.get("subject_id") or data.get("object_id", "?")
    link = (
        build_resource_link(settings.user_ui_public_base_url, subject_type, subject_id)
        if change_code != "deleted" and subject_type in _OBJECT_LABELS
        else None
    ) or ""
    what = f"{label} {title!r}" if title and subject_type == object_type else label
    fallback_subject = f"Beobachtet: {what} – {change}"
    fallback_body = f"Beobachtetes Objekt ({label}, id={data.get('object_id', '?')}): {change}."
    if title and subject_type != object_type:
        fallback_body += f"\nDokument: {title}"
    if link:
        fallback_body += f"\n\nÖffnen: {link}"
    placeholders = {
        "change": change,
        "object_type": label,
        "object_id": data.get("object_id", "?"),
        "title": title,
        "changed_by": data.get("changed_by") or "",
        "link": link,
    }

    targets = [("in_app", user)]
    auth_client = AuthServiceClient(settings.auth_service_base_url)
    try:
        email = await auth_client.email_for(user)
    except Exception:  # noqa: BLE001 - in-app still goes out
        logger.warning("watch_email_lookup_failed", extra={"user": user}, exc_info=True)
        email = None
    finally:
        await auth_client.close()
    if email:
        targets.append(("email", email))
    async with session_factory() as session:
        for channel, recipient in targets:
            subject, body = await _render_or_fallback(
                session,
                use_case="favorite.watch.triggered",
                recipient=recipient,
                fallback_subject=fallback_subject,
                fallback_body=fallback_body,
                **placeholders,
            )
            notification = await repository.create_and_send(
                session,
                settings,
                channel=channel,
                recipient=recipient,
                subject=subject,
                body=body,
                rate_limiter=rate_limiter,
            )
            await session.commit()
            await publish_notification_result(publish_event, notification)


async def _handle_reminder_due(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Follow-up reminder due (P90-S1, ADR 0246): in-app to its owner, and an
    e-mail when the owner's account has an address. The reminder stays
    listed as due in the user-ui until it is done or snoozed, so a failed
    mail loses a message, not the reminder. The object's title isn't
    resolved here (it would need a call per object type, as the owner); the
    note and the link carry what the person wrote down."""
    data = event.payload
    user = data.get("user_id", "?")
    object_type = data.get("object_type", "document")
    object_id = data.get("object_id", "?")
    note = data.get("note") or ""
    due_at = data.get("due_at", "")
    label = _OBJECT_LABELS.get(object_type, object_type)
    fallback_subject = f"Wiedervorlage: {note[:80]}" if note else f"Wiedervorlage: {label}"
    fallback_body = f"Ihre Wiedervorlage ist fällig ({label}, id={object_id})."
    if note:
        fallback_body += f"\n\nNotiz: {note}"
    link = (
        build_resource_link(settings.user_ui_public_base_url, object_type, object_id)
        if object_type in _OBJECT_LABELS
        else None
    )
    if link:
        fallback_body += f"\n\n{label} öffnen: {link}"
    placeholders = {
        "note": note,
        "object_type": label,
        "object_id": object_id,
        "due_at": due_at,
        "user": user,
        "link": link or "",
    }

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="favorite.reminder.due",
            recipient=user,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            **placeholders,
        )
        in_app = await repository.create_and_send(
            session,
            settings,
            channel="in_app",
            recipient=user,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, in_app)

    auth_client = AuthServiceClient(settings.auth_service_base_url)
    try:
        email = await auth_client.email_for(user)
    except Exception:  # noqa: BLE001 - the in-app message went out; log and move on
        logger.warning("reminder_email_lookup_failed", extra={"user": user}, exc_info=True)
        email = None
    finally:
        await auth_client.close()
    if not email:
        return
    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case="favorite.reminder.due",
            recipient=email,
            fallback_subject=fallback_subject,
            fallback_body=fallback_body,
            **placeholders,
        )
        email_notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=email,
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


_INSTANCE_OPERATION_LABELS = {
    "skip": "übersprungen",
    "send_back": "zurückgesendet",
    "migrate": "auf eine neue Prozessversion migriert",
}


async def _handle_instance_modified(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Administrative instance change (P93-S2, ADR 0249 Part A c): an
    administrator skipped a step, sent the instance back or migrated it.
    Everyone whose claim was released by the change gets one in-app notice
    (same shape as `_handle_task_claim_abandoned`); without affected
    claimants there is nobody to tell - the audit ledger keeps the record."""
    data = event.payload
    claimants = data.get("affected_claimants") or []
    if not claimants:
        return
    operation = data.get("operation", "?")
    label = _INSTANCE_OPERATION_LABELS.get(operation, operation)
    reason = data.get("reason", "")
    business_key = data.get("business_key")
    link = build_resource_link(settings.reviewer_ui_public_base_url, "instance", event.subject)

    for claimant in claimants:
        principal_id = claimant.get("principal_id")
        if not principal_id:
            continue
        task_name = claimant.get("task_name", "?")
        fallback_subject = f"Vorgang geändert: {task_name}"
        fallback_body = (
            f"Der Vorgang {event.subject} (business_key={business_key!r}) wurde "
            f"administrativ {label}. Ihre Beanspruchung des Tasks {task_name!r} "
            f"wurde dabei aufgehoben.\n\nBegründung: {reason}"
        )
        if link:
            fallback_body += f"\n\nVorgang öffnen: {link}"
        async with session_factory() as session:
            subject, body = await _render_or_fallback(
                session,
                use_case="workflow.instance.modified",
                recipient=principal_id,
                fallback_subject=fallback_subject,
                fallback_body=fallback_body,
                task_name=task_name,
                instance_id=event.subject,
                business_key=business_key,
                operation=operation,
                reason=reason,
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


async def _handle_approval_requested(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """A new four-eyes request (P90-S3, ADR 0247): tells the people who may
    (or are meant to) decide, in-app and by e-mail. permission-service
    resolved them when the request was made (`recipients`, principal ids,
    initiator excluded); they are mapped to user names and addresses through
    auth-service's directory. An id with no directory entry (a technical
    account) still gets the in-app message under its id. The link opens the
    reviewer-ui's approval inbox, where requests are decided."""
    data = event.payload
    recipients = [r for r in data.get("recipients") or [] if r]
    if not recipients:
        logger.info(
            "approval_requested_without_recipients",
            extra={"request_id": data.get("request_id"), "action_type": data.get("action_type")},
        )
        return
    action_type = data.get("action_type", "?")
    request_id = data.get("request_id", "?")
    auth_client = AuthServiceClient(settings.auth_service_base_url)
    try:
        directory = await auth_client.directory_by_id()
    except Exception:  # noqa: BLE001 - without names: in-app under the ids, no mail
        logger.warning("approval_recipient_lookup_failed", exc_info=True)
        directory = {}
    finally:
        await auth_client.close()
    initiator_id = data.get("initiated_by", "?")
    initiated_by = (directory.get(initiator_id) or {}).get("username") or initiator_id
    link = (
        f"{settings.reviewer_ui_public_base_url.rstrip('/')}/approvals/"
        if settings.reviewer_ui_public_base_url
        else ""
    )
    fallback_subject = f"Freigabe erbeten: {action_type}"
    fallback_body = (
        f"{initiated_by} bittet um Freigabe ({action_type}, Anfrage {request_id}). "
        "Die Aktion wird erst ausgeführt, wenn eine zweite Person sie freigibt."
    )
    if link:
        fallback_body += f"\n\nZur Freigabe: {link}"

    for principal_id in recipients:
        entry = directory.get(principal_id) or {}
        username = entry.get("username") or principal_id
        targets = [("in_app", username)]
        if entry.get("email"):
            targets.append(("email", entry["email"]))
        async with session_factory() as session:
            for channel, recipient in targets:
                subject, body = await _render_or_fallback(
                    session,
                    use_case="permission.approval.requested",
                    recipient=recipient,
                    fallback_subject=fallback_subject,
                    fallback_body=fallback_body,
                    action_type=action_type,
                    initiated_by=initiated_by,
                    request_id=request_id,
                    link=link,
                    recipient_name=username,
                )
                notification = await repository.create_and_send(
                    session,
                    settings,
                    channel=channel,
                    recipient=recipient,
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


_STATE_NAMES = {
    "ok": "OK",
    "under": "zu wenige Instanzen",
    "vanished": "keine erreichbare Instanz",
    "over": "zu viele Instanzen",
    "unmonitored": "nicht überwacht",
}

# (subject, body) fallbacks per replica event; the body's placeholders are
# filled from the event payload (P87-S3, ADR 0241).
_REPLICA_FALLBACKS: dict[str, tuple[str, str]] = {
    "registry.replicas.under": (
        "Zu wenige Instanzen: {service_type}",
        "{service_type} hat {reachable} erreichbare Instanz(en), erwartet sind {expected} "
        "(Zustand: {state}).",
    ),
    "registry.replicas.over": (
        "Zu viele Instanzen: {service_type}",
        "{service_type} hat {reachable} erreichbare Instanzen, erwartet sind {expected}.",
    ),
    "registry.replicas.restored": (
        "Wieder in Ordnung: {service_type}",
        "{service_type} ist wieder im erwarteten Bereich: {reachable} erreichbare Instanz(en), "
        "erwartet {expected} (vorher: {previous_state}).",
    ),
}


def _expected_label(minimum: object, maximum: object) -> str:
    if minimum is None:
        return "keine Vorgabe"
    if maximum is None:
        return f"mindestens {minimum}"
    if maximum == minimum:
        return str(minimum)
    return f"{minimum} bis {maximum}"


async def _handle_replica_alarm(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Replica alarms from registry-service (P87-S3, ADR 0241 Decision 4):
    too few, too many, and back in range. registry-service publishes each
    debounced transition once, so every event here is one mail. The
    recipient is the operations address - a service type is not a person,
    same fixed-address pattern as `storage_admin_email`. `restored` closes
    the loop, so a mailbox shows the alarm ended."""
    data = event.payload
    values = {
        "service_type": str(data.get("service_type", event.subject)),
        "reachable": str(data.get("reachable", "?")),
        "expected": _expected_label(data.get("min"), data.get("max")),
        "state": _STATE_NAMES.get(str(data.get("state")), str(data.get("state", "?"))),
        "previous_state": _STATE_NAMES.get(
            str(data.get("previous_state")), str(data.get("previous_state", "?"))
        ),
    }
    fallback_subject, fallback_body = _REPLICA_FALLBACKS[event.event_type]

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case=event.event_type,
            recipient=settings.operations_admin_email,
            fallback_subject=fallback_subject.format(**values),
            fallback_body=fallback_body.format(**values),
            **values,
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.operations_admin_email,
            subject=subject,
            body=body,
            rate_limiter=rate_limiter,
        )
        await session.commit()
        await publish_notification_result(publish_event, notification)


_LEVEL_NAMES = {"ok": "OK", "warning": "Warnung", "error": "Fehler", "unknown": "unbekannt"}

# (subject, body) fallbacks per sensor alert event (P88-S5, ADR 0245).
_SENSOR_ALERT_FALLBACKS: dict[str, tuple[str, str]] = {
    "monitoring.alert.raised": (
        "{level}: {name}",
        "Der Sensor {sensor}{filter} steht auf {value} ({level}, seit {since}). "
        "Schwellwerte: {thresholds}.",
    ),
    "monitoring.alert.resolved": (
        "Wieder in Ordnung: {name}",
        "Der Sensor {sensor}{filter} steht wieder auf {value} (vorher: {previous_level}).",
    ),
}


def _format_number(value: object) -> str:
    if value is None:
        return "?"
    number = float(value)
    return f"{number:.0f}" if number.is_integer() else f"{number:.2f}"


def _thresholds_label(thresholds: dict) -> str:
    parts = []
    for key, label in (
        ("min_error", "Fehler unter"),
        ("min_warning", "Warnung unter"),
        ("max_warning", "Warnung über"),
        ("max_error", "Fehler über"),
    ):
        if thresholds.get(key) is not None:
            parts.append(f"{label} {_format_number(thresholds[key])}")
    return ", ".join(parts) or "keine"


async def _handle_sensor_alert(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    publish_event: Callable[[str, str, dict], Awaitable[None]],
    rate_limiter: RecipientRateLimiter,
    event: Event,
) -> None:
    """Sensor alerts from monitoring-service (P88-S5, ADR 0245): a rule
    reached warning/error, or is back to ok after a notified alarm.
    monitoring-service publishes one event per notified level (downtime
    windows already applied), so every event here is one mail, to the same
    operations address as the replica alarms."""
    data = event.payload
    label_filter = data.get("label_filter") or {}
    values = {
        "name": str(data.get("name", event.subject)),
        "sensor": str(data.get("sensor", "?")),
        "filter": (
            " (" + ", ".join(f"{k}={v}" for k, v in label_filter.items()) + ")"
            if label_filter
            else ""
        ),
        "value": _format_number(data.get("value")),
        "level": _LEVEL_NAMES.get(str(data.get("level")), str(data.get("level", "?"))),
        "previous_level": _LEVEL_NAMES.get(
            str(data.get("previous_level")), str(data.get("previous_level", "?"))
        ),
        "since": str(data.get("since", "?")),
        "thresholds": _thresholds_label(data.get("thresholds") or {}),
    }
    fallback_subject, fallback_body = _SENSOR_ALERT_FALLBACKS[event.event_type]

    async with session_factory() as session:
        subject, body = await _render_or_fallback(
            session,
            use_case=event.event_type,
            recipient=settings.operations_admin_email,
            fallback_subject=fallback_subject.format(**values),
            fallback_body=fallback_body.format(**values),
            **values,
        )
        notification = await repository.create_and_send(
            session,
            settings,
            channel="email",
            recipient=settings.operations_admin_email,
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
