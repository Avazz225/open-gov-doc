from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from archival_service.models import ArchivalTransfer

_ACTIVE_STATUSES = ("pending", "locked", "copied", "verified")


class NotFoundError(Exception):
    pass


async def get_transfer(session: AsyncSession, transfer_id: str) -> ArchivalTransfer:
    transfer = await session.get(ArchivalTransfer, transfer_id)
    if transfer is None:
        raise NotFoundError(transfer_id)
    return transfer


async def list_transfers(
    session: AsyncSession, *, status: str | None = None
) -> list[ArchivalTransfer]:
    query = select(ArchivalTransfer)
    if status is not None:
        query = query.where(ArchivalTransfer.status == status)
    result = await session.execute(query.order_by(ArchivalTransfer.created_at.desc()))
    return list(result.scalars().all())


async def get_active_transfer_for_document(
    session: AsyncSession, document_id: str
) -> ArchivalTransfer | None:
    """Ein Dokument darf nie zwei gleichzeitig laufende Transfers haben -
    verhindert doppelte Archiv-Kopien bei mehrfachen `due-for-archival`-
    Treffern ueber mehrere Ticks hinweg."""
    result = await session.execute(
        select(ArchivalTransfer).where(
            ArchivalTransfer.document_id == document_id,
            ArchivalTransfer.status.in_(_ACTIVE_STATUSES),
        )
    )
    return result.scalars().first()


async def create_transfer(session: AsyncSession, document_id: str) -> ArchivalTransfer:
    now = datetime.now(UTC)
    transfer = ArchivalTransfer(
        document_id=document_id, status="pending", created_at=now, updated_at=now
    )
    session.add(transfer)
    await session.flush()
    return transfer


async def list_active_transfers(session: AsyncSession) -> list[ArchivalTransfer]:
    """Transfers, die noch einen Fortschrittsschritt brauchen (5.6) - jeder
    Poll-Tick versucht, sie einen Schritt weiterzubewegen."""
    result = await session.execute(
        select(ArchivalTransfer).where(ArchivalTransfer.status.in_(_ACTIVE_STATUSES))
    )
    return list(result.scalars().all())


async def list_due_for_dehydration(
    session: AsyncSession, *, delay_days: int
) -> list[ArchivalTransfer]:
    cutoff = datetime.now(UTC) - timedelta(days=delay_days)
    result = await session.execute(
        select(ArchivalTransfer).where(
            ArchivalTransfer.status == "released",
            ArchivalTransfer.released_at.is_not(None),
            ArchivalTransfer.released_at <= cutoff,
        )
    )
    return list(result.scalars().all())


async def update_status(session: AsyncSession, transfer: ArchivalTransfer, **fields) -> None:
    for key, value in fields.items():
        setattr(transfer, key, value)
    transfer.updated_at = datetime.now(UTC)
    await session.flush()


async def mark_failed(
    session: AsyncSession, transfer: ArchivalTransfer, *, error_message: str
) -> None:
    transfer.status = "failed"
    transfer.error_message = error_message
    transfer.updated_at = datetime.now(UTC)
    await session.flush()
