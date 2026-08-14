from dataclasses import dataclass
from datetime import UTC, datetime

from dms_eventbus_client import Event
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from audit_service.hashchain import GENESIS_HASH, compute_hash
from audit_service.models import AuditEvent, AuditMeta

_AUDIT_META_ID = 1


def _hashable_fields(
    event_id: str,
    event_type: str,
    occurred_at: datetime,
    service_name: str,
    subject: str | None,
    payload: dict,
    recorded_at: datetime,
    *,
    actor: str | None = None,
    include_actor: bool = False,
    on_behalf_of: str | None = None,
    include_on_behalf_of: bool = False,
) -> dict:
    fields = {
        "event_id": event_id,
        "event_type": event_type,
        "occurred_at": occurred_at.isoformat(),
        "service_name": service_name,
        "subject": subject,
        "payload": payload,
        "recorded_at": recorded_at.isoformat(),
    }
    # Cutover versioning (since P7-S2, see AuditMeta): the actor field may
    # only flow into the canonical JSON for rows AFTER the cutover,
    # otherwise the hash input for already-chained old rows changes and
    # every previous verification retroactively fails.
    if include_actor:
        fields["actor"] = actor
    # Same cutover versioning for the on_behalf_of field introduced in
    # P14-S11 (4.4a) - its own, independent cutover value (see
    # get_on_behalf_of_field_cutover_id).
    if include_on_behalf_of:
        fields["on_behalf_of"] = on_behalf_of
    return fields


async def get_actor_field_cutover_id(session: AsyncSession) -> int:
    """Creates the AuditMeta row once, on the very first call after the
    migration - cutover = MAX(id) of the rows existing at that point (0 if
    the chain is empty). ``ON CONFLICT DO NOTHING`` makes this
    concurrency-safe (multiple simultaneous first calls create the same
    value only once, no race between two computations)."""
    existing = await session.get(AuditMeta, _AUDIT_META_ID)
    if existing is not None:
        return existing.actor_field_cutover_id

    max_id_result = await session.execute(select(func.max(AuditEvent.id)))
    cutover = max_id_result.scalar() or 0

    stmt = (
        pg_insert(AuditMeta)
        .values(id=_AUDIT_META_ID, actor_field_cutover_id=cutover)
        .on_conflict_do_nothing(index_elements=["id"])
    )
    await session.execute(stmt)
    await session.flush()

    row = await session.get(AuditMeta, _AUDIT_META_ID)
    assert row is not None
    return row.actor_field_cutover_id


async def get_on_behalf_of_field_cutover_id(session: AsyncSession) -> int:
    """Like ``get_actor_field_cutover_id`` above, but for the new
    ``on_behalf_of`` field (4.4a, since P14-S11) - its own, independent
    cutover value. Unlike the actor cutover (which CREATES the AuditMeta
    row via INSERT on the very first call), the row already exists here
    (since P7-S2) - the cutover is therefore RETROFITTED via an ORM
    attribute update instead of newly inserted, and set exactly once thanks
    to the ``is None`` condition."""
    await get_actor_field_cutover_id(session)  # ensures the row exists
    existing = await session.get(AuditMeta, _AUDIT_META_ID)
    assert existing is not None
    if existing.on_behalf_of_field_cutover_id is not None:
        return existing.on_behalf_of_field_cutover_id

    max_id_result = await session.execute(select(func.max(AuditEvent.id)))
    cutover = max_id_result.scalar() or 0
    existing.on_behalf_of_field_cutover_id = cutover
    await session.flush()
    return cutover


async def _latest(session: AsyncSession) -> AuditEvent | None:
    result = await session.execute(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1))
    return result.scalar_one_or_none()


async def append_event(session: AsyncSession, event: Event) -> AuditEvent:
    """Appends ``event`` to the end of the chain. Idempotent by
    ``event_id`` - JetStream may deliver duplicates under at-least-once
    delivery, which are skipped here without re-chaining.
    """
    existing = await session.execute(
        select(AuditEvent).where(AuditEvent.event_id == event.event_id)
    )
    found = existing.scalar_one_or_none()
    if found is not None:
        return found

    # Ensures the cutover point (P7-S2) is fixed BEFORE this new row is
    # assigned an id - otherwise a later first-time computation of the
    # cutover by verify_chain() could wrongly classify rows already hashed
    # with actor as "before the cutover" (main.py's lifespan already calls
    # this explicitly beforehand, but this call here is the actual
    # guarantee - idempotent on repetition).
    await get_actor_field_cutover_id(session)
    await get_on_behalf_of_field_cutover_id(session)

    latest = await _latest(session)
    prev_hash = latest.hash if latest is not None else GENESIS_HASH
    recorded_at = datetime.now(UTC)

    # Every newly appended row is by definition after both cutover points
    # (its id has not yet been assigned, so it is necessarily > every
    # existing id) - include_actor/include_on_behalf_of are therefore
    # always True for append_event.
    fields = _hashable_fields(
        event.event_id,
        event.event_type,
        event.occurred_at,
        event.service_name,
        event.subject,
        event.payload,
        recorded_at,
        actor=event.actor,
        include_actor=True,
        on_behalf_of=event.on_behalf_of,
        include_on_behalf_of=True,
    )
    entry = AuditEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        service_name=event.service_name,
        subject=event.subject,
        payload=event.payload,
        actor=event.actor,
        on_behalf_of=event.on_behalf_of,
        recorded_at=recorded_at,
        prev_hash=prev_hash,
        hash=compute_hash(prev_hash, fields),
    )
    session.add(entry)
    await session.flush()
    return entry


async def list_events(
    session: AsyncSession,
    *,
    limit: int = 100,
    actor: str | None = None,
    on_behalf_of: str | None = None,
    subject: str | None = None,
    event_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[AuditEvent]:
    """Filter API (5.4b, since P7-S2): ``event_type`` supports the same
    NATS wildcard notation as the subject configuration itself
    (``"document.>"`` -> all ``document.*`` events), otherwise an exact
    match. ``on_behalf_of`` (since P14-S11, 4.4a) filters on actions
    performed on behalf of a specific person - independent of ``actor``,
    which remains the delegate who actually performed the action."""
    query = select(AuditEvent)
    if actor is not None:
        query = query.where(AuditEvent.actor == actor)
    if on_behalf_of is not None:
        query = query.where(AuditEvent.on_behalf_of == on_behalf_of)
    if subject is not None:
        query = query.where(AuditEvent.subject == subject)
    if event_type is not None:
        if event_type.endswith(".>"):
            query = query.where(AuditEvent.event_type.like(f"{event_type[:-1]}%"))
        else:
            query = query.where(AuditEvent.event_type == event_type)
    if since is not None:
        query = query.where(AuditEvent.occurred_at >= since)
    if until is not None:
        query = query.where(AuditEvent.occurred_at <= until)
    query = query.order_by(AuditEvent.id.desc()).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


@dataclass
class ChainVerificationResult:
    ok: bool
    checked: int
    broken_at_id: int | None = None


async def verify_chain(session: AsyncSession) -> ChainVerificationResult:
    cutover_id = await get_actor_field_cutover_id(session)
    on_behalf_of_cutover_id = await get_on_behalf_of_field_cutover_id(session)
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
            actor=entry.actor,
            include_actor=entry.id > cutover_id,
            on_behalf_of=entry.on_behalf_of,
            include_on_behalf_of=entry.id > on_behalf_of_cutover_id,
        )
        expected_hash = compute_hash(expected_prev, fields)
        if entry.prev_hash != expected_prev or entry.hash != expected_hash:
            return ChainVerificationResult(ok=False, checked=len(entries), broken_at_id=entry.id)
        expected_prev = entry.hash

    return ChainVerificationResult(ok=True, checked=len(entries))
