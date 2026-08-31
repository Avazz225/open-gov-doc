import uuid
from datetime import UTC, datetime

from mail_connector.models import (
    InboundAttachment,
    InboundMessage,
    MailRoutingLogEntry,
    OutboundMessage,
)
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession


class NotFoundError(Exception):
    pass


class DuplicateInTargetMailboxError(Exception):
    """Routing target (14.2, Post-Roadmap Phase 31 Session 12b) already has
    a message with the identical `source_uid` (`route_message`, checked
    BEFORE the update, not just relying on the DB constraint - a raw
    `IntegrityError` would surface as an unhandled 500) - found live during
    this session's own verification: two mailboxes independently polling
    the same physical mail account (or a message legitimately duplicated to
    both) can each ingest their own copy of a message sharing the same
    backend-native UID."""


class NotInStatusError(Exception):
    """Confirm/Assign/Reject requires a specific starting status (2.5) -
    e.g. `confirm_match` only on a not-yet-confirmed `proposed_match`
    message."""


async def get_by_source_uid(
    session: AsyncSession, mailbox_id: str, source_uid: str
) -> InboundMessage | None:
    """Since Post-Roadmap Phase 31 Session 12a, scoped to `mailbox_id` -
    `source_uid` is only unique WITHIN a mailbox (see `models.
    InboundMessage`)."""
    result = await session.execute(
        select(InboundMessage).where(
            InboundMessage.mailbox_id == mailbox_id, InboundMessage.source_uid == source_uid
        )
    )
    return result.scalars().first()


async def create_inbound_message(
    session: AsyncSession,
    *,
    mailbox_id: str,
    source_uid: str,
    from_address: str,
    subject: str,
    body_text: str,
    received_at: datetime,
    match_type: str | None,
    match_value: str | None,
    proposed_target_type: str | None,
    proposed_target_id: str | None,
    match_candidates: list[str],
) -> InboundMessage:
    message = InboundMessage(
        id=str(uuid.uuid4()),
        mailbox_id=mailbox_id,
        source_uid=source_uid,
        from_address=from_address,
        subject=subject,
        body_text=body_text,
        received_at=received_at,
        status="proposed_match" if proposed_target_id is not None else "unassigned",
        match_type=match_type,
        match_value=match_value,
        proposed_target_type=proposed_target_type,
        proposed_target_id=proposed_target_id,
        match_candidates=match_candidates,
    )
    session.add(message)
    await session.flush()
    return message


async def add_attachment(
    session: AsyncSession,
    *,
    message_id: str,
    filename: str,
    content_type: str | None,
    size_bytes: int,
    scan_id: str,
    scan_status: str,
    storage_object_key: str | None,
) -> InboundAttachment:
    attachment = InboundAttachment(
        message_id=message_id,
        filename=filename,
        content_type=content_type,
        size_bytes=size_bytes,
        scan_id=scan_id,
        scan_status=scan_status,
        storage_object_key=storage_object_key,
    )
    session.add(attachment)
    await session.flush()
    return attachment


async def get_message(session: AsyncSession, message_id: str) -> InboundMessage:
    message = await session.get(InboundMessage, message_id)
    if message is None:
        raise NotFoundError(f"message_id {message_id!r} unbekannt")
    return message


async def list_attachments(session: AsyncSession, message_id: str) -> list[InboundAttachment]:
    result = await session.execute(
        select(InboundAttachment).where(InboundAttachment.message_id == message_id)
    )
    return list(result.scalars().all())


async def route_message(
    session: AsyncSession,
    message_id: str,
    *,
    target_mailbox_id: str,
    routed_by: str,
    reason: str | None,
) -> InboundMessage:
    """Hands a message off to a different mailbox (14.2, Post-Roadmap Phase
    31 Session 12b) - records the hop in `MailRoutingLogEntry` and updates
    `InboundMessage.mailbox_id` to the new current location. Status/match/
    proposed-target fields are left untouched: routing is orthogonal to
    matching, a document/case reference candidate found in the message text
    doesn't become invalid just because a different mailbox now administers
    it. Status validity (message not already `confirmed`/`rejected`) and
    target-mailbox validity (a configured mailbox, not the current one) are
    checked at `main.py`'s call site, same pattern as `confirm_match`/
    `assign_manually`/`reject_message`.

    Pre-checks the target mailbox for an existing message with the same
    `source_uid` (raises `DuplicateInTargetMailboxError` rather than letting
    the composite unique constraint surface as a raw, unhandled
    `IntegrityError`/500 - found live during this session's own
    verification, see the exception's docstring)."""
    message = await get_message(session, message_id)
    existing_at_target = await session.execute(
        select(InboundMessage).where(
            InboundMessage.mailbox_id == target_mailbox_id,
            InboundMessage.source_uid == message.source_uid,
        )
    )
    if existing_at_target.scalars().first() is not None:
        raise DuplicateInTargetMailboxError(
            f"Im Zielpostfach {target_mailbox_id!r} existiert bereits eine Nachricht mit "
            f"identischem source_uid {message.source_uid!r} - Weiterleitung abgelehnt"
        )
    log_entry = MailRoutingLogEntry(
        message_id=message_id,
        from_mailbox_id=message.mailbox_id,
        to_mailbox_id=target_mailbox_id,
        routed_by=routed_by,
        routed_at=datetime.now(UTC),
        reason=reason,
    )
    session.add(log_entry)
    message.mailbox_id = target_mailbox_id
    await session.flush()
    return message


async def list_routing_log(session: AsyncSession, message_id: str) -> list[MailRoutingLogEntry]:
    result = await session.execute(
        select(MailRoutingLogEntry)
        .where(MailRoutingLogEntry.message_id == message_id)
        .order_by(MailRoutingLogEntry.routed_at)
    )
    return list(result.scalars().all())


async def search_routing_log(
    session: AsyncSession,
    *,
    mailbox_id: str | None = None,
    routed_by: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: str | None = None,
    limit: int = 200,
) -> list[tuple[MailRoutingLogEntry, InboundMessage]]:
    """The searchable, standalone cross-message "Postbuch" register (14.2,
    Post-Roadmap Phase 31 Session 12c) - unlike `list_routing_log` (scoped
    to one message, embedded as `InboundMessageOut.routing_log`), this joins
    every hop across every message so a mail-room principal can find "what
    happened to this piece of correspondence" without already knowing its
    `message_id`. `mailbox_id` matches a hop touching that mailbox on
    EITHER side (it was routed FROM it or TO it) - a department wants to
    see both what left and what arrived. `q` is a case-insensitive substring
    match against the message's subject (same `ilike` mechanism SQL already
    offers, no new search infrastructure - a dedicated full-text engine is
    `search-service`'s job for `document-service`/`case-service` content,
    not raw inbound mail). Ordered newest-first, same convention as
    `audit-service`'s `GET /events` (the closest existing precedent for a
    filtered, capped register view)."""
    query = select(MailRoutingLogEntry, InboundMessage).join(
        InboundMessage, MailRoutingLogEntry.message_id == InboundMessage.id
    )
    if mailbox_id is not None:
        query = query.where(
            or_(
                MailRoutingLogEntry.from_mailbox_id == mailbox_id,
                MailRoutingLogEntry.to_mailbox_id == mailbox_id,
            )
        )
    if routed_by is not None:
        query = query.where(MailRoutingLogEntry.routed_by == routed_by)
    if since is not None:
        query = query.where(MailRoutingLogEntry.routed_at >= since)
    if until is not None:
        query = query.where(MailRoutingLogEntry.routed_at <= until)
    if q is not None:
        query = query.where(InboundMessage.subject.ilike(f"%{q}%"))
    query = query.order_by(MailRoutingLogEntry.routed_at.desc()).limit(limit)
    result = await session.execute(query)
    return [(entry, message) for entry, message in result.all()]


async def list_messages(
    session: AsyncSession, *, status: str | None = None, mailbox_id: str | None = None
) -> list[InboundMessage]:
    query = select(InboundMessage)
    if status is not None:
        query = query.where(InboundMessage.status == status)
    if mailbox_id is not None:
        query = query.where(InboundMessage.mailbox_id == mailbox_id)
    result = await session.execute(query.order_by(InboundMessage.received_at.desc()))
    return list(result.scalars().all())


async def mark_confirmed(
    session: AsyncSession, message_id: str, *, confirmed_by: str
) -> InboundMessage:
    message = await get_message(session, message_id)
    message.status = "confirmed"
    message.confirmed_by = confirmed_by
    message.confirmed_at = datetime.now(UTC)
    await session.flush()
    return message


async def mark_rejected(
    session: AsyncSession, message_id: str, *, rejected_by: str, reason: str | None
) -> InboundMessage:
    message = await get_message(session, message_id)
    message.status = "rejected"
    message.confirmed_by = rejected_by
    message.confirmed_at = datetime.now(UTC)
    message.rejected_reason = reason
    await session.flush()
    return message


async def set_attachment_document(
    session: AsyncSession, attachment_id: int, *, document_id: str
) -> None:
    attachment = await session.get(InboundAttachment, attachment_id)
    if attachment is not None:
        attachment.resulting_document_id = document_id
        attachment.storage_object_key = None
        await session.flush()


async def create_outbound_message(
    session: AsyncSession,
    *,
    to_address: str,
    subject: str,
    body: str,
    related_document_id: str | None,
    related_case_id: str | None,
    sent_by: str,
    status: str,
    error_message: str | None,
) -> OutboundMessage:
    message = OutboundMessage(
        id=str(uuid.uuid4()),
        to_address=to_address,
        subject=subject,
        body=body,
        related_document_id=related_document_id,
        related_case_id=related_case_id,
        sent_by=sent_by,
        sent_at=datetime.now(UTC),
        status=status,
        error_message=error_message,
    )
    session.add(message)
    await session.flush()
    return message


async def list_outbound_messages(session: AsyncSession) -> list[OutboundMessage]:
    result = await session.execute(select(OutboundMessage).order_by(OutboundMessage.sent_at.desc()))
    return list(result.scalars().all())
