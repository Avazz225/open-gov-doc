import uuid
from calendar import monthrange
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from reporting_service.models import DocumentCreatedEvent, ReportRun, ReportSchedule

_GROUP_BY_FORMAT = {
    "day": "YYYY-MM-DD",
    "week": "IYYY-IW",
    "month": "YYYY-MM",
}


class NotFoundError(Exception):
    pass


async def record_document_created(
    session: AsyncSession, *, document_id: str, folder_id: str | None, occurred_at: datetime
) -> None:
    session.add(
        DocumentCreatedEvent(document_id=document_id, folder_id=folder_id, occurred_at=occurred_at)
    )
    await session.flush()


async def get_document_volume(
    session: AsyncSession,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    folder_id: str | None = None,
    group_by: str = "day",
) -> list[tuple[str, str | None, int]]:
    period = func.to_char(DocumentCreatedEvent.occurred_at, _GROUP_BY_FORMAT[group_by])
    query = select(period.label("period"), DocumentCreatedEvent.folder_id, func.count())
    if since is not None:
        query = query.where(DocumentCreatedEvent.occurred_at >= since)
    if until is not None:
        query = query.where(DocumentCreatedEvent.occurred_at <= until)
    if folder_id is not None:
        query = query.where(DocumentCreatedEvent.folder_id == folder_id)
    query = query.group_by(period, DocumentCreatedEvent.folder_id).order_by(period)
    result = await session.execute(query)
    return list(result.all())


async def create_schedule(
    session: AsyncSession,
    *,
    report_type: str,
    format: str,
    frequency: str,
    recipient_email: str,
    filters: dict,
) -> ReportSchedule:
    schedule = ReportSchedule(
        report_type=report_type,
        format=format,
        frequency=frequency,
        recipient_email=recipient_email,
        filters=filters,
        next_run_at=datetime.now(UTC),
        created_at=datetime.now(UTC),
    )
    session.add(schedule)
    await session.flush()
    return schedule


async def list_schedules(session: AsyncSession) -> list[ReportSchedule]:
    result = await session.execute(select(ReportSchedule).order_by(ReportSchedule.created_at))
    return list(result.scalars().all())


async def get_schedule(session: AsyncSession, schedule_id: str) -> ReportSchedule:
    schedule = await session.get(ReportSchedule, schedule_id)
    if schedule is None:
        raise NotFoundError(f"Planung {schedule_id!r} nicht gefunden")
    return schedule


async def delete_schedule(session: AsyncSession, schedule_id: str) -> None:
    schedule = await get_schedule(session, schedule_id)
    await session.delete(schedule)


async def list_due_schedules(session: AsyncSession, *, now: datetime) -> list[ReportSchedule]:
    result = await session.execute(select(ReportSchedule).where(ReportSchedule.next_run_at <= now))
    return list(result.scalars().all())


def advance_next_run(current: datetime, frequency: str) -> datetime:
    """Naechster Faelligkeitszeitpunkt nach `current` - Monats-Inkrement
    behandelt Tagesueberlauf (z. B. 31. Januar -> 28./29. Februar) explizit,
    da Python-`datetime` das nicht eingebaut kennt und eine zusaetzliche
    Bibliothek (z. B. dateutil) fuer diesen einen Anwendungsfall unnoetig
    waere."""
    if frequency == "daily":
        return current + timedelta(days=1)
    if frequency == "weekly":
        return current + timedelta(days=7)
    month = current.month + 1
    year = current.year + (1 if month > 12 else 0)
    month = 1 if month > 12 else month
    day = min(current.day, monthrange(year, month)[1])
    return current.replace(year=year, month=month, day=day)


async def mark_schedule_run(
    session: AsyncSession, schedule: ReportSchedule, *, ran_at: datetime
) -> None:
    schedule.last_run_at = ran_at
    schedule.next_run_at = advance_next_run(schedule.next_run_at, schedule.frequency)
    await session.flush()


async def create_report_run(
    session: AsyncSession,
    *,
    schedule_id: str | None,
    report_type: str,
    format: str,
    storage_object_key: str,
    content_type: str,
) -> ReportRun:
    run = ReportRun(
        id=str(uuid.uuid4()),
        schedule_id=schedule_id,
        report_type=report_type,
        format=format,
        storage_object_key=storage_object_key,
        content_type=content_type,
        generated_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    return run


async def get_report_run(session: AsyncSession, run_id: str) -> ReportRun:
    run = await session.get(ReportRun, run_id)
    if run is None:
        raise NotFoundError(f"Berichtslauf {run_id!r} nicht gefunden")
    return run
