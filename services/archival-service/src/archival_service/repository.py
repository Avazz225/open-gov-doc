from datetime import UTC, datetime, timedelta

from dms_retry import compute_backoff_seconds
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from archival_service.models import ArchivalTransfer, CaseArchivalTransfer

_ACTIVE_STATUSES = ("pending", "locked", "copied", "verified")
_ACTIVE_CASE_STATUSES = ("pending", "locked", "packaged", "verified")


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
    Poll-Tick versucht, sie einen Schritt weiterzubewegen. Seit Post-Roadmap
    Phase 20 Session 2 zusaetzlich durch `next_retry_at` gefiltert - ein
    Transfer, der gerade erst einen Backoff-Zeitpunkt fuer den naechsten
    Versuch bekommen hat (siehe `mark_failed`), wird vor dessen Ablauf
    uebersprungen statt in jedem Tick erneut sofort zu scheitern."""
    now = datetime.now(UTC)
    result = await session.execute(
        select(ArchivalTransfer).where(
            ArchivalTransfer.status.in_(_ACTIVE_STATUSES),
            or_(ArchivalTransfer.next_retry_at.is_(None), ArchivalTransfer.next_retry_at <= now),
        )
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
    session: AsyncSession,
    transfer: ArchivalTransfer,
    *,
    error_message: str,
    max_attempts: int,
) -> None:
    """Post-Roadmap Phase 20 Session 2 (ADR 0078): ein Fehlschlag verlaesst
    NICHT mehr sofort die aktuelle Phase (`status` bleibt z. B. `locked`,
    damit `list_active_transfers` den Transfer weiterhin erfasst) - stattdessen
    zaehlt `attempts` hoch und setzt per `compute_backoff_seconds` einen
    `next_retry_at`. Erst nach `max_attempts` erfolglosen Versuchen wechselt
    `status` auf das echte Terminalstatus `failed_permanent`."""
    transfer.attempts += 1
    transfer.error_message = error_message
    transfer.updated_at = datetime.now(UTC)
    if transfer.attempts >= max_attempts:
        transfer.status = "failed_permanent"
        transfer.next_retry_at = None
    else:
        delay = compute_backoff_seconds(transfer.attempts - 1)
        transfer.next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
    await session.flush()


async def reset_for_retry(session: AsyncSession, transfer: ArchivalTransfer) -> None:
    """Manueller Neustart eines `failed_permanent`-Transfers (`POST
    /archival-transfers/{id}/retry`, Post-Roadmap Phase 20 Session 2) - setzt
    ihn auf `pending` zurueck, damit die Pipeline von vorne beginnt (jede
    Phase holt ihre Eingaben ohnehin frisch, ein Neustart ist idempotent -
    einfacher als die genaue unterbrochene Phase zu rekonstruieren, die beim
    Uebergang auf `failed_permanent` nicht mehr separat vorgehalten wird)."""
    transfer.status = "pending"
    transfer.attempts = 0
    transfer.next_retry_at = None
    transfer.error_message = None
    transfer.updated_at = datetime.now(UTC)
    await session.flush()


async def get_case_transfer(session: AsyncSession, transfer_id: str) -> CaseArchivalTransfer:
    transfer = await session.get(CaseArchivalTransfer, transfer_id)
    if transfer is None:
        raise NotFoundError(transfer_id)
    return transfer


async def list_case_transfers(
    session: AsyncSession, *, status: str | None = None
) -> list[CaseArchivalTransfer]:
    query = select(CaseArchivalTransfer)
    if status is not None:
        query = query.where(CaseArchivalTransfer.status == status)
    result = await session.execute(query.order_by(CaseArchivalTransfer.created_at.desc()))
    return list(result.scalars().all())


async def get_active_case_transfer_for_case(
    session: AsyncSession, case_id: str
) -> CaseArchivalTransfer | None:
    result = await session.execute(
        select(CaseArchivalTransfer).where(
            CaseArchivalTransfer.case_id == case_id,
            CaseArchivalTransfer.status.in_(_ACTIVE_CASE_STATUSES),
        )
    )
    return result.scalars().first()


async def create_case_transfer(session: AsyncSession, case_id: str) -> CaseArchivalTransfer:
    now = datetime.now(UTC)
    transfer = CaseArchivalTransfer(
        case_id=case_id, status="pending", created_at=now, updated_at=now
    )
    session.add(transfer)
    await session.flush()
    return transfer


async def list_active_case_transfers(session: AsyncSession) -> list[CaseArchivalTransfer]:
    now = datetime.now(UTC)
    result = await session.execute(
        select(CaseArchivalTransfer).where(
            CaseArchivalTransfer.status.in_(_ACTIVE_CASE_STATUSES),
            or_(
                CaseArchivalTransfer.next_retry_at.is_(None),
                CaseArchivalTransfer.next_retry_at <= now,
            ),
        )
    )
    return list(result.scalars().all())
