import uuid
from datetime import UTC, datetime

from mail_connector.models import InboundAttachment, InboundMessage, OutboundMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class NotFoundError(Exception):
    pass


class NotInStatusError(Exception):
    """Confirm/Assign/Reject setzt einen bestimmten Ausgangsstatus voraus
    (2.5) - z. B. `confirm_match` nur auf einer noch unbestätigten
    `proposed_match`-Nachricht."""


async def get_by_source_uid(session: AsyncSession, source_uid: str) -> InboundMessage | None:
    result = await session.execute(
        select(InboundMessage).where(InboundMessage.source_uid == source_uid)
    )
    return result.scalars().first()


async def create_inbound_message(
    session: AsyncSession,
    *,
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


async def list_messages(
    session: AsyncSession, *, status: str | None = None
) -> list[InboundMessage]:
    query = select(InboundMessage)
    if status is not None:
        query = query.where(InboundMessage.status == status)
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
