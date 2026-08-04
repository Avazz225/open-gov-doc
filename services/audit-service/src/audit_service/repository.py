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
    # Cutover-Versionierung (seit P7-S2, siehe AuditMeta): das actor-Feld
    # darf nur fuer Zeilen NACH dem Cutover ins kanonische JSON einfliessen,
    # sonst aendert sich der Hash-Input fuer bereits verkettete Alt-Zeilen
    # und jede vorherige Pruefung schlaegt rueckwirkend fehl.
    if include_actor:
        fields["actor"] = actor
    return fields


async def get_actor_field_cutover_id(session: AsyncSession) -> int:
    """Legt die AuditMeta-Zeile beim allerersten Aufruf nach der Migration
    einmalig an - Cutover = MAX(id) der zu diesem Zeitpunkt bestehenden
    Zeilen (0, falls die Kette leer ist). ``ON CONFLICT DO NOTHING`` macht
    das nebenlaeufigkeitssicher (mehrere gleichzeitige erste Aufrufe legen
    denselben Wert nur einmal an, kein Race zwischen zwei Berechnungen)."""
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

    # Stellt sicher, dass der Cutover-Punkt (P7-S2) VOR dieser neuen Zeile
    # feststeht, bevor deren id vergeben wird - sonst koennte eine spaetere
    # verify_chain()-Erstberechnung des Cutovers faelschlich auch bereits
    # mit actor gehashte Zeilen als "vor dem Cutover" einstufen (die
    # main.py-Lifespan ruft dies zwar bereits vorab explizit auf, dieser
    # Aufruf hier ist die eigentliche Garantie - idempotent bei Wiederholung).
    await get_actor_field_cutover_id(session)

    latest = await _latest(session)
    prev_hash = latest.hash if latest is not None else GENESIS_HASH
    recorded_at = datetime.now(UTC)

    # Jede neu angehaengte Zeile liegt per Definition nach dem Cutover (ihre
    # id ist noch nicht vergeben, also zwangslaeufig > jeder bestehenden id)
    # - include_actor ist fuer append_event daher immer True.
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
    )
    entry = AuditEvent(
        event_id=event.event_id,
        event_type=event.event_type,
        occurred_at=event.occurred_at,
        service_name=event.service_name,
        subject=event.subject,
        payload=event.payload,
        actor=event.actor,
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
    subject: str | None = None,
    event_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[AuditEvent]:
    """Filter-API (5.4b, seit P7-S2): ``event_type`` unterstuetzt dieselbe
    NATS-Wildcard-Schreibweise wie die Subject-Konfiguration selbst
    (``"document.>"`` -> alle ``document.*``-Ereignisse), sonst exakter
    Treffer."""
    query = select(AuditEvent)
    if actor is not None:
        query = query.where(AuditEvent.actor == actor)
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
    query = query.order_by(AuditEvent.id).limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


@dataclass
class ChainVerificationResult:
    ok: bool
    checked: int
    broken_at_id: int | None = None


async def verify_chain(session: AsyncSession) -> ChainVerificationResult:
    cutover_id = await get_actor_field_cutover_id(session)
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
        )
        expected_hash = compute_hash(expected_prev, fields)
        if entry.prev_hash != expected_prev or entry.hash != expected_hash:
            return ChainVerificationResult(ok=False, checked=len(entries), broken_at_id=entry.id)
        expected_prev = entry.hash

    return ChainVerificationResult(ok=True, checked=len(entries))
