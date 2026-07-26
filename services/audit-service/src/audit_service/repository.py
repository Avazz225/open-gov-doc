from dataclasses import dataclass
from datetime import UTC, datetime

from dms_eventbus_client import Event
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from audit_service.hashchain import GENESIS_HASH, compute_hash
from audit_service.models import AuditEvent


def _hashable_fields(
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    service_name: str,
    subject: str | None,
    payload: dict,
    recorded_at: datetime,
) -> dict:
    return {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at.isoformat(),
        "service_name": service_name,
        "subject": subject,
        "payload": payload,
        "recorded_at": recorded_at.isoformat(),
    }


async def _latest(session: AsyncSession) -> AuditEvent | None:
    result = await session.execute(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    return result.scalar_one_or_none()


async def append_event(session: AsyncSession, event: Event) -> AuditEvent:
    """Hängt ``event`` ans Ende der Kette an. Idempotent nach ``event_id`` -
    JetStream liefert bei At-least-once-Zustellung ggf. Duplikate, die hier
    ohne erneute Verkettung übersprungen werden.
    """
    existing = await session.execute(
        select(AuditEvent).where(AuditEvent.event_id == event.event_id)
    )
    found = existing.scalar_one_or_none()
    if found is not None:
        return found

    latest = await _latest(session)
    prev_hash = latest.hash if latest is not None else GENESIS_HASH
    recorded_at = datetime.now(UTC)

    fields = _hashable_fields(
        event.event_id,
        event.event_type,
        event.occurred_at,
        event.service_name,
        event.subject,
        event.payload,
        recorded_at,
    )
    entry = AuditEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        service_name=event.service_name,
        subject=event.subject,
        payload=event.payload,
        recorded_at=recorded_at,
        prev_hash=prev_hash,
        hash=compute_hash(prev_hash, fields),
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_events(session: AsyncSession, *, limit: int = 100) -> list[AuditEvent]:
    result = await session.execute(select(AuditEvent).order_by(AuditEvent.id).limit(limit))
    return list(result.scalars().all())


@dataclass
class ChainVerificationResult:
    ok: bool
    checked: int
    broken_at_id: int | None = None


async def verify_chain(session: AsyncSession) -> ChainVerificationResult:
    result = await session.execute(select(AuditEvent).order_by(AuditEvent.id))
    entries = list(result.scalars().all())

    expected_prev = GENESIS_HASH
    for entry in entries:
        fields = _hashable_fields(
            entry.event_id,
            entry.event_type,
            entry.occurred_at,
            entry.service_name,
            entry.subject,
            entry.payload,
            entry.recorded_at,
        )
        expected_hash = compute_hash(expected_prev, fields)
        if entry.prev_hash != expected_prev or entry.hash != expected_hash:
            return ChainVerificationResult(ok=False, checked=len(entries), broken_at_id=entry.id)
        expected_prev = entry.hash

    return ChainVerificationResult(ok=True, checked=len(entries))
