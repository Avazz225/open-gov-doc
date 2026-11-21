"""Follow-up reminders (Wiedervorlagen, P90-S1, ADR 0246) - persistence and
the due poll. The poll is the same timer pattern as document-service's
deletion reminder (5.2a) and workflow-service's SLA monitoring (7.1), as
Konzept 2.5 suggests: a loop finds what is due, marks it, and publishes an
event that notification-service turns into a message."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from favorite_service.models import Reminder
from favorite_service.schemas import ReminderCreate

# One poller across replicas: whoever gets this transaction-scoped advisory
# lock evaluates the tick, the others skip it (same as monitoring-service's
# alert evaluator, ADR 0245). The value is arbitrary but fixed.
REMINDER_POLL_LOCK_KEY = 0x0F4F_D90E
# A tick handles at most this many; the rest follows on the next tick.
DUE_BATCH_SIZE = 500


class NotFoundError(Exception):
    pass


class NotOpenError(Exception):
    pass


async def create_reminder(
    session: AsyncSession, payload: ReminderCreate, *, now: datetime
) -> Reminder:
    reminder = Reminder(
        user_id=payload.user_id,
        object_type=payload.object_type,
        object_id=payload.object_id,
        due_at=payload.due_at,
        note=payload.note,
        status="open",
        created_at=now,
        updated_at=now,
    )
    session.add(reminder)
    await session.flush()
    return reminder


async def get_own_reminder(session: AsyncSession, reminder_id: str, *, user_id: str) -> Reminder:
    """Someone else's reminder answers like a missing one (404): its existence
    is nobody else's business."""
    reminder = await session.get(Reminder, reminder_id)
    if reminder is None or reminder.user_id != user_id:
        raise NotFoundError(f"Wiedervorlage {reminder_id!r} nicht gefunden")
    return reminder


async def list_reminders(
    session: AsyncSession,
    *,
    user_id: str,
    status: str | None = None,
    due_only: bool = False,
    object_type: str | None = None,
    object_id: str | None = None,
    now: datetime,
) -> list[Reminder]:
    query = select(Reminder).where(Reminder.user_id == user_id)
    if status is not None:
        query = query.where(Reminder.status == status)
    if due_only:
        query = query.where(Reminder.status == "open", Reminder.due_at <= now)
    if object_type is not None:
        query = query.where(Reminder.object_type == object_type)
    if object_id is not None:
        query = query.where(Reminder.object_id == object_id)
    query = query.order_by(Reminder.due_at, Reminder.created_at)
    return list((await session.execute(query)).scalars().all())


async def count_due(session: AsyncSession, *, user_id: str, now: datetime) -> int:
    return int(
        await session.scalar(
            select(func.count())
            .select_from(Reminder)
            .where(Reminder.user_id == user_id, Reminder.status == "open", Reminder.due_at <= now)
        )
        or 0
    )


def _require_open(reminder: Reminder) -> None:
    if reminder.status != "open":
        raise NotOpenError(f"Wiedervorlage {reminder.id!r} ist bereits erledigt")


def reschedule(reminder: Reminder, due_at: datetime, *, now: datetime) -> None:
    """A new due time re-arms the notification (`notified_at` cleared)."""
    _require_open(reminder)
    reminder.due_at = due_at
    reminder.notified_at = None
    reminder.updated_at = now


def update_note(reminder: Reminder, note: str, *, now: datetime) -> None:
    _require_open(reminder)
    reminder.note = note
    reminder.updated_at = now


def complete(reminder: Reminder, *, now: datetime) -> None:
    _require_open(reminder)
    reminder.status = "done"
    reminder.completed_at = now
    reminder.updated_at = now


def reopen(reminder: Reminder, *, now: datetime) -> None:
    """Undo a "done" (a misclick in the list). Keeps the due time; if it has
    passed, the reminder is due again at once but not notified again."""
    if reminder.status == "open":
        return
    reminder.status = "open"
    reminder.completed_at = None
    reminder.updated_at = now


@dataclass(frozen=True)
class DueReminder:
    id: str
    user_id: str
    object_type: str
    object_id: str
    due_at: datetime
    note: str


async def claim_due(session: AsyncSession, *, now: datetime) -> list[DueReminder] | None:
    """One poll tick: marks open, unnotified, due reminders as notified and
    returns them. `None` when another replica holds the tick's lock. The
    caller commits, then publishes - a lost event after the commit costs a
    message, not the reminder: it is still listed as due."""
    got_lock = await session.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": REMINDER_POLL_LOCK_KEY}
    )
    if not got_lock:
        return None
    rows = (
        (
            await session.execute(
                select(Reminder)
                .where(
                    Reminder.status == "open",
                    Reminder.notified_at.is_(None),
                    Reminder.due_at <= now,
                )
                .order_by(Reminder.due_at)
                .limit(DUE_BATCH_SIZE)
            )
        )
        .scalars()
        .all()
    )
    claimed = []
    for reminder in rows:
        reminder.notified_at = now
        claimed.append(
            DueReminder(
                id=reminder.id,
                user_id=reminder.user_id,
                object_type=reminder.object_type,
                object_id=reminder.object_id,
                due_at=reminder.due_at,
                note=reminder.note,
            )
        )
    await session.flush()
    return claimed


def utcnow() -> datetime:
    return datetime.now(UTC)
