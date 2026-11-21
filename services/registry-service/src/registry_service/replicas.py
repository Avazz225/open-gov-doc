"""Instance replacement and expected replicas (P87-S2, ADR 0241).

Two jobs, both evaluated per `service_type` by the poll loop in `main.py`:

- **Forgetting** (Decision 1): once a type has at least one reachable,
  `active` instance, it keeps only as many unreachable entries as its
  expectation is short - `max(0, min - R)`, the most recently seen ones -
  and forgets every other entry that has been unreachable for longer than
  `forget_after_seconds`. A restart, a rolling restart and a scale-down all
  come out of this one rule; a type with no reachable instance keeps
  everything ("vanished").
- **Alarms** (Decision 4): the observed state (`ok`/`under`/`vanished`/
  `over`/`unmonitored`) becomes the committed state only after it has held
  for `replica_alarm_after_seconds`. Each committed transition is returned
  once, so the caller publishes exactly one event per transition.

The pure functions (`expectation_for`, `observed_state`, `forgettable`) hold
the rules; the async functions apply them to the database.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from registry_service.models import ExpectedReplicas, ReplicaAlarmState, ServiceInstance

ALARM_STATES = frozenset({"under", "vanished", "over"})


@dataclass(frozen=True)
class Expectation:
    min: int | None
    max: int | None
    source: str  # "admin" | "default" | "none"


@dataclass(frozen=True)
class TypeCounts:
    reachable: int
    draining: int
    unreachable: int


@dataclass(frozen=True)
class Transition:
    service_type: str
    old: str
    new: str


def expectation_for(
    service_type: str,
    overrides: Mapping[str, ExpectedReplicas],
    defaults: Mapping[str, Mapping[str, int | None]],
) -> Expectation:
    """Admin override first, then the Helm default, else none (Decision 3)."""
    override = overrides.get(service_type)
    if override is not None:
        return Expectation(override.min_replicas, override.max_replicas, "admin")
    default = defaults.get(service_type)
    if default is not None and default.get("min") is not None:
        return Expectation(int(default["min"]), default.get("max"), "default")
    return Expectation(None, None, "none")


def observed_state(reachable: int, expectation: Expectation) -> str:
    """The state a type is in right now, before debouncing (Decision 4)."""
    if expectation.min is None:
        return "unmonitored"
    if reachable == 0:
        return "vanished"
    if reachable < expectation.min:
        return "under"
    if expectation.max is not None and reachable > expectation.max:
        return "over"
    return "ok"


def _is_reachable(instance: ServiceInstance, timeout_seconds: float, now: datetime) -> bool:
    return (now - instance.last_heartbeat_at).total_seconds() <= timeout_seconds


def counts(
    instances: Iterable[ServiceInstance], *, timeout_seconds: float, now: datetime
) -> TypeCounts:
    reachable = draining = unreachable = 0
    for instance in instances:
        if not _is_reachable(instance, timeout_seconds, now):
            unreachable += 1
        elif instance.status == "draining":
            draining += 1
        else:
            reachable += 1
    return TypeCounts(reachable, draining, unreachable)


def forgettable(
    instances: Iterable[ServiceInstance],
    *,
    expected_min: int | None,
    timeout_seconds: float,
    forget_after_seconds: float,
    now: datetime,
) -> list[ServiceInstance]:
    """Decision 1 for the instances of ONE type. No expectation counts as
    min = 1. Draining instances are leaving on purpose and are neither
    reachable nor dead here."""
    instances = list(instances)
    reachable = sum(
        1 for i in instances if _is_reachable(i, timeout_seconds, now) and i.status != "draining"
    )
    if reachable == 0:
        return []
    dead = sorted(
        (i for i in instances if not _is_reachable(i, timeout_seconds, now)),
        key=lambda i: i.last_heartbeat_at,
        reverse=True,
    )
    keep = max(0, (expected_min or 1) - reachable)
    return [
        i for i in dead[keep:] if (now - i.last_heartbeat_at).total_seconds() > forget_after_seconds
    ]


async def load_overrides(session: AsyncSession) -> dict[str, ExpectedReplicas]:
    rows = (await session.execute(select(ExpectedReplicas))).scalars().all()
    return {row.service_type: row for row in rows}


async def instances_by_type(session: AsyncSession) -> dict[str, list[ServiceInstance]]:
    rows = (await session.execute(select(ServiceInstance))).scalars().all()
    grouped: dict[str, list[ServiceInstance]] = {}
    for row in rows:
        grouped.setdefault(row.service_type, []).append(row)
    return grouped


async def forget_replaced(
    session: AsyncSession,
    *,
    defaults: Mapping[str, Mapping[str, int | None]],
    timeout_seconds: float,
    forget_after_seconds: float,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """Deletes every entry Decision 1 lets go and returns
    `(instance_id, service_type)` for each, so the caller can publish
    `registry.instance.forgotten`. The caller commits."""
    now = now or datetime.now(UTC)
    overrides = await load_overrides(session)
    forgotten: list[tuple[str, str]] = []
    for service_type, instances in (await instances_by_type(session)).items():
        expectation = expectation_for(service_type, overrides, defaults)
        for instance in forgettable(
            instances,
            expected_min=expectation.min,
            timeout_seconds=timeout_seconds,
            forget_after_seconds=forget_after_seconds,
            now=now,
        ):
            forgotten.append((instance.instance_id, service_type))
    if forgotten:
        await session.execute(
            delete(ServiceInstance).where(
                ServiceInstance.instance_id.in_([instance_id for instance_id, _ in forgotten])
            )
        )
    return forgotten


async def overview(
    session: AsyncSession,
    *,
    defaults: Mapping[str, Mapping[str, int | None]],
    timeout_seconds: float,
    now: datetime | None = None,
) -> list[tuple[str, Expectation, TypeCounts]]:
    """Every type that has instances, an override or a default - a type that
    is expected but never registered counts as vanished."""
    now = now or datetime.now(UTC)
    overrides = await load_overrides(session)
    grouped = await instances_by_type(session)
    types = sorted(set(grouped) | set(overrides) | set(defaults))
    return [
        (
            service_type,
            expectation_for(service_type, overrides, defaults),
            counts(grouped.get(service_type, []), timeout_seconds=timeout_seconds, now=now),
        )
        for service_type in types
    ]


async def advance_alarm(
    session: AsyncSession,
    service_type: str,
    observed: str,
    *,
    debounce_seconds: float,
    now: datetime | None = None,
) -> Transition | None:
    """Moves one type's alarm state one step (Decision 4) and returns the
    transition if THIS call committed it. The caller commits the session."""
    now = now or datetime.now(UTC)
    await session.execute(
        insert(ReplicaAlarmState)
        .values(service_type=service_type, state="unmonitored", since=now)
        .on_conflict_do_nothing(index_elements=["service_type"])
    )
    row = await session.get(ReplicaAlarmState, service_type, populate_existing=True)
    if observed == row.state:
        if row.pending_state is not None:
            row.pending_state = None
            row.pending_since = None
        return None
    if row.pending_state != observed:
        row.pending_state = observed
        row.pending_since = now
        return None
    if (now - row.pending_since).total_seconds() < debounce_seconds:
        return None
    old = row.state
    # Conditional commit: with several registry replicas only the one whose
    # UPDATE still sees the old state wins and reports the transition.
    result = await session.execute(
        update(ReplicaAlarmState)
        .where(ReplicaAlarmState.service_type == service_type, ReplicaAlarmState.state == old)
        .values(state=observed, since=now, pending_state=None, pending_since=None)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        return None
    await session.refresh(row)
    return Transition(service_type, old, observed)


def event_for(transition: Transition) -> str | None:
    """The event a committed transition publishes, or None (Decision 4).
    `under` covers `vanished` too (with `reachable: 0`); leaving an alarm
    state for `ok` or `unmonitored` is `restored`; anything else between
    non-alarm states (e.g. `unmonitored` -> `ok`) is silent."""
    if transition.new in ("under", "vanished"):
        return "registry.replicas.under"
    if transition.new == "over":
        return "registry.replicas.over"
    if transition.old in ALARM_STATES:
        return "registry.replicas.restored"
    return None


async def current_states(session: AsyncSession) -> dict[str, ReplicaAlarmState]:
    rows = (await session.execute(select(ReplicaAlarmState))).scalars().all()
    return {row.service_type: row for row in rows}
