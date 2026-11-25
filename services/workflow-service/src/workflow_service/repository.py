import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import defer
from sqlalchemy.orm.exc import StaleDataError

from workflow_service import spiff_adapter
from workflow_service.models import (
    BusinessCalendar,
    CirculationStep,
    DmnDefinition,
    FederationTask,
    ProcessDefinition,
    ProcessInstance,
    ReadyTask,
    TaskClaim,
)


@dataclass
class TimerAdvanceResult:
    """Result of an SLA time monitoring poll tick (P6-S2) for a single
    instance - `main.py` translates this into `workflow.task.escalated`/
    `workflow.instance.completed` events, without knowing SpiffWorkflow
    types itself."""

    instance: ProcessInstance
    fired: list[spiff_adapter.FiredBoundaryEvent]
    newly_completed: bool


class NotFoundError(Exception):
    pass


class InvalidBpmnError(Exception):
    """The uploaded BPMN file cannot be parsed or the referenced/
    automatically resolved process ID is invalid. Wraps
    `spiff_adapter.BpmnParseError`, so `spiff_adapter` doesn't need to know
    this module's error hierarchy."""


class ProcessDefinitionInUseError(Exception):
    """Deletion rejected because process instances still exist."""


class DmnDefinitionInUseError(Exception):
    """P64-S1 (4.5): deletion rejected because a saved process definition's
    BPMN still references this DMN family's `decision_id` via
    `camunda:decisionRef`, AND the version being deleted is currently the
    LATEST version of its family - an already-superseded version is never
    loaded by `list_latest_dmn_xml()` (see its own docstring), so deleting
    one is always safe regardless of references."""


class InvalidDmnError(Exception):
    """The uploaded DMN file cannot be parsed or does not contain exactly
    one `<decision>` (P14-S4). Wraps `spiff_adapter.DmnParseError`, analogous
    to `InvalidBpmnError`."""


class DuplicateDecisionIdError(Exception):
    """The `decision_id` extracted from the DMN file collides with the
    respective NEWEST version of another DMN family (P14-S4, see
    `models.DmnDefinition`) - for every BPMN parse, SpiffWorkflow always
    loads only the newest version of each family into the same parser
    (`list_latest_dmn_xml`); two families with a colliding `decision_id`
    would no longer be distinguishable there."""


class DuplicateBusinessCalendarNameError(Exception):
    """`name` of a business calendar (P14-S5) is already taken - unlike
    process/DMN definitions, NO versioning pattern (see
    `models.BusinessCalendar`), so a name is permanently unique instead of
    automatically getting a new version."""


class InvalidBusinessCalendarError(Exception):
    """An entry in `non_working_dates` is not a valid ISO date
    (`YYYY-MM-DD`, P14-S5)."""


class TaskNotReadyError(Exception):
    """The specified task is not (or no longer) found among this
    instance's currently ready Manual/User Tasks - already completed,
    wrong ID, or the instance has already finished."""


class TaskAlreadyClaimedError(Exception):
    """The task already has an active claim by a DIFFERENT principal
    (Post-Roadmap Phase 31 Session 10) - claiming is exclusive, one
    principal at a time."""


class InstanceNotRunningError(Exception):
    """`POST /instances/{id}/retry` (P12-S2) on an already `completed`
    instance - nothing to retry."""


class ConcurrentModificationError(Exception):
    """RBAC/correctness (Phase 60 Session 3, ADR 0185) - raised when
    `ProcessInstance.workflow_version`'s optimistic-concurrency check
    (`models.ProcessInstance.__mapper_args__`) rejects a write during
    `complete_task`/`retry_instance` because another request already
    modified the same instance since it was read (e.g. two Manual Tasks
    from the same parallel gateway completed back-to-back). The caller
    already sees a real `409` - not silently overwritten data - and can
    retry against the now-current state."""


# Postgres advisory lock namespaces (P25-S1, ADR 0096) for
# `create_process_definition`/`create_dmn_definition` - two fixed `key1`
# values for the two-integer variant of `pg_advisory_xact_lock(key1, key2)`,
# so that a process definition and a DMN definition that happen to share
# the same `name` do NOT lock each other out (both families are versioned
# independently, see `models.py`). `key2` is `hashtext(name)` in each case -
# arbitrary, but deterministic per family name.
_PROCESS_DEFINITION_LOCK_NAMESPACE = 1
_DMN_DEFINITION_LOCK_NAMESPACE = 2
# Post-Roadmap Phase 44 Session 4 (ADR 0096's own documented, deliberately
# unfixed "Consequences" gap): a THIRD namespace, keyed on the DMN's own
# extracted `decision_id` rather than `name` - `_DMN_DEFINITION_LOCK_
# NAMESPACE` above only serializes concurrent creates within the SAME
# family (`name`), which does nothing for two concurrent first-time
# creations of DIFFERENT families whose `dmn_xml` happens to extract the
# same `decision_id` (each takes ITS OWN `name`-keyed lock, so neither
# blocks the other) - exactly the cross-family race ADR 0096 named as
# out of scope for that session.
_DMN_DECISION_ID_LOCK_NAMESPACE = 3


async def create_process_definition(
    session: AsyncSession, *, name: str, bpmn_xml: str, process_id: str | None
) -> ProcessDefinition:
    """Since P6-S8, ``name`` is the process family key (a 2.1a-style
    versioning pattern, like with document versions) - a call under an
    already-existing name automatically creates the next version instead
    of being rejected. Earlier versions remain retrievable/startable
    unchanged, no overwriting.

    **P25-S1 (ADR 0096)**: before reading `max(version)`, a transaction-
    scoped Postgres advisory lock is taken on `name`
    (`pg_advisory_xact_lock`), which is automatically released on
    COMMIT/ROLLBACK of this transaction. Unlike `object_type_service`'s
    `_next_sequence_number`/`case_service`'s `_next_case_sequence_number`,
    there is NO separate, always-already-existing counter row here that
    could be locked via `SELECT ... FOR UPDATE`: for a brand-new family
    (not a single `ProcessDefinition` row with this `name` yet), under
    Postgres' default isolation level (READ COMMITTED, no predicate
    locking) there would simply be nothing that `SELECT ... FOR UPDATE`
    could lock - two concurrent first-creations of the same new family
    would both compute `next_version = 1` and still fail on the
    `(name, version)` unique constraint. The advisory lock instead locks
    purely via the `name` hash, independent of the current row inventory,
    and thereby covers both the first creation and subsequent versions."""
    await session.execute(
        select(func.pg_advisory_xact_lock(_PROCESS_DEFINITION_LOCK_NAMESPACE, func.hashtext(name)))
    )
    max_version = await session.execute(
        select(func.max(ProcessDefinition.version)).where(ProcessDefinition.name == name)
    )
    next_version = (max_version.scalar_one() or 0) + 1

    dmn_xmls = await list_latest_dmn_xml(session)
    try:
        spec, resolved_process_id = spiff_adapter.parse_bpmn(
            bpmn_xml, process_id, dmn_definitions=dmn_xmls
        )
        lane_bindings = spiff_adapter.parse_lane_bindings(bpmn_xml)
    except spiff_adapter.BpmnParseError as exc:
        raise InvalidBpmnError(str(exc)) from exc

    now = datetime.now(UTC)
    definition = ProcessDefinition(
        name=name,
        version=next_version,
        bpmn_process_id=resolved_process_id,
        bpmn_xml=bpmn_xml,
        lane_bindings=lane_bindings,
        is_circulation=spiff_adapter.has_circulation_task(spec),
        created_at=now,
        updated_at=now,
    )
    session.add(definition)
    await session.flush()
    return definition


async def get_process_definition(
    session: AsyncSession, process_definition_id: int
) -> ProcessDefinition:
    definition = await session.get(ProcessDefinition, process_definition_id)
    if definition is None:
        raise NotFoundError(f"process_definition_id {process_definition_id!r} unbekannt")
    return definition


async def list_process_definitions(
    session: AsyncSession, *, name: str | None = None
) -> list[ProcessDefinition]:
    """Without a `name` filter, only the respective newest version is
    returned per process family (`DISTINCT ON`, Postgres-specific like
    elsewhere in this project, e.g. `INSERT ... ON CONFLICT DO NOTHING`) -
    a growing version history should not clutter the overview list. With
    a `name` filter, the complete version history of that one family is
    returned instead, newest version first."""
    if name is not None:
        result = await session.execute(
            select(ProcessDefinition)
            .where(ProcessDefinition.name == name)
            .order_by(ProcessDefinition.version.desc())
        )
        return list(result.scalars().all())

    result = await session.execute(
        select(ProcessDefinition)
        .distinct(ProcessDefinition.name)
        .order_by(ProcessDefinition.name, ProcessDefinition.version.desc())
    )
    return list(result.scalars().all())


async def delete_process_definition(session: AsyncSession, process_definition_id: int) -> None:
    definition = await get_process_definition(session, process_definition_id)
    existing_instances = await session.execute(
        select(ProcessInstance.id)
        .where(ProcessInstance.process_definition_id == process_definition_id)
        .limit(1)
    )
    if existing_instances.scalar_one_or_none() is not None:
        raise ProcessDefinitionInUseError(
            f"Prozessdefinition {process_definition_id!r} hat noch Instanzen - Löschung abgelehnt"
        )
    await session.delete(definition)
    await session.flush()


async def create_dmn_definition(session: AsyncSession, *, name: str, dmn_xml: str) -> DmnDefinition:
    """Same versioning pattern as `create_process_definition` (`name` is the
    family key). Additionally checks that the extracted `decision_id` is
    unique among the respective newest versions of ALL families (see
    `DuplicateDecisionIdError`) - an older, no-longer-current version of
    another family may collide here, since `list_latest_dmn_xml` never
    loads it anyway.

    **P25-S1 (ADR 0096)**: same advisory-lock protection against the
    version race condition as `create_process_definition` - see its
    docstring for the reasoning why `pg_advisory_xact_lock` is used here
    instead of `SELECT ... FOR UPDATE` on a counter row.

    **Post-Roadmap Phase 44 Session 4**: a SECOND advisory lock, taken
    on the extracted `decision_id` right after parsing and before the
    cross-family `DuplicateDecisionIdError` check below, closes ADR
    0096's own named remaining gap - two concurrent creates of
    DIFFERENT families (different `name`, so the lock above doesn't
    serialize them against each other) whose `dmn_xml` extracts the
    SAME `decision_id` now genuinely serialize on that second lock, so
    the second call's `latest_others` read below is guaranteed to see
    the first call's already-committed row instead of racing against
    an uncommitted one."""
    await session.execute(
        select(func.pg_advisory_xact_lock(_DMN_DEFINITION_LOCK_NAMESPACE, func.hashtext(name)))
    )
    max_version = await session.execute(
        select(func.max(DmnDefinition.version)).where(DmnDefinition.name == name)
    )
    next_version = (max_version.scalar_one() or 0) + 1

    try:
        decision_id = spiff_adapter.parse_dmn(dmn_xml)
    except spiff_adapter.DmnParseError as exc:
        raise InvalidDmnError(str(exc)) from exc

    await session.execute(
        select(
            func.pg_advisory_xact_lock(_DMN_DECISION_ID_LOCK_NAMESPACE, func.hashtext(decision_id))
        )
    )
    latest_others = await list_latest_dmn_definitions(session)
    for other in latest_others:
        if other.name != name and other.decision_id == decision_id:
            raise DuplicateDecisionIdError(
                f"decision_id {decision_id!r} wird bereits von DMN-Familie {other.name!r} verwendet"
            )

    now = datetime.now(UTC)
    definition = DmnDefinition(
        name=name,
        version=next_version,
        decision_id=decision_id,
        dmn_xml=dmn_xml,
        created_at=now,
        updated_at=now,
    )
    session.add(definition)
    await session.flush()
    return definition


async def get_dmn_definition(session: AsyncSession, dmn_definition_id: int) -> DmnDefinition:
    definition = await session.get(DmnDefinition, dmn_definition_id)
    if definition is None:
        raise NotFoundError(f"dmn_definition_id {dmn_definition_id!r} unbekannt")
    return definition


async def list_latest_dmn_definitions(session: AsyncSession) -> list[DmnDefinition]:
    result = await session.execute(
        select(DmnDefinition)
        .distinct(DmnDefinition.name)
        .order_by(DmnDefinition.name, DmnDefinition.version.desc())
    )
    return list(result.scalars().all())


async def list_dmn_definitions(
    session: AsyncSession, *, name: str | None = None
) -> list[DmnDefinition]:
    """Analogous to `list_process_definitions`: without a `name` filter,
    only the respective newest version per family, with a filter the
    complete version history."""
    if name is not None:
        result = await session.execute(
            select(DmnDefinition)
            .where(DmnDefinition.name == name)
            .order_by(DmnDefinition.version.desc())
        )
        return list(result.scalars().all())
    return await list_latest_dmn_definitions(session)


async def delete_dmn_definition(session: AsyncSession, dmn_definition_id: int) -> None:
    """**P64-S1 (4.5)**: now DOES check "in use", closing the gap this
    docstring used to document as a deliberate limitation. Only blocks
    when the version being deleted is the LATEST of its family AND its
    `decision_id` is still referenced by some saved process definition's
    `camunda:decisionRef` (`spiff_adapter.extract_decision_refs`) -
    deleting an already-superseded version stays unrestricted, since
    `list_latest_dmn_xml()` never loads it anyway (see its own docstring),
    so it cannot be the thing a `businessRuleTask` actually resolves
    against. Already-running instances remain unaffected either way (their
    `workflow_state` already fully contains the decision loaded at start
    time) - this only prevents a FUTURE `start_instance` from failing with
    a SpiffWorkflow `ValidationException` (translated: `InvalidBpmnError`)
    at the next attempt."""
    definition = await get_dmn_definition(session, dmn_definition_id)
    latest = await list_latest_dmn_definitions(session)
    is_latest_of_family = any(d.id == definition.id for d in latest)
    if is_latest_of_family:
        process_definitions = await list_process_definitions(session)
        referenced = any(
            definition.decision_id in spiff_adapter.extract_decision_refs(pd.bpmn_xml)
            for pd in process_definitions
        )
        if referenced:
            raise DmnDefinitionInUseError(
                f"DMN-Definition {dmn_definition_id!r} (decision_id="
                f"{definition.decision_id!r}) wird noch von einer gespeicherten "
                f"Prozessdefinition referenziert - Löschung abgelehnt"
            )
    await session.delete(definition)
    await session.flush()


async def list_dmn_references(
    session: AsyncSession, dmn_definition_id: int
) -> list[ProcessDefinition]:
    """P71-S3 (7.1): read-time counterpart to `delete_dmn_definition`'s own
    "in use" check above - which (latest-per-family) process definitions
    currently reference this DMN version's `decision_id` via
    `camunda:decisionRef`, reusing the exact same lookup so the two never
    drift apart. Deliberately not restricted to "latest version of its
    DMN family" like the delete check is - a historical DMN version can
    still be inspected here even if it's no longer the one SpiffWorkflow
    would actually resolve against (`list_latest_dmn_xml`), since the
    question asked here ("is anything using THIS row") is honest either
    way, unlike the delete check's narrower "would deleting THIS row
    break anything runnable" question."""
    definition = await get_dmn_definition(session, dmn_definition_id)
    process_definitions = await list_process_definitions(session)
    return [
        pd
        for pd in process_definitions
        if definition.decision_id in spiff_adapter.extract_decision_refs(pd.bpmn_xml)
    ]


async def list_latest_dmn_xml(session: AsyncSession) -> list[str]:
    """DMN XML contents of the respective newest version of each family -
    loaded before every BPMN parse (`create_process_definition`/
    `start_instance`), since at parse time it is not known which
    `decisionRef`s a `businessRuleTask` in the BPMN file actually
    references (see `spiff_adapter` module docstring - unreferenced
    decisions are harmless, loading them only costs some parse time)."""
    definitions = await list_latest_dmn_definitions(session)
    return [d.dmn_xml for d in definitions]


def _parse_non_working_dates(non_working_dates: list[str]) -> None:
    """Validation only (P14-S5) - `spiff_adapter.register_business_calendars()`
    expects `date` objects, `non_working_dates` itself remains persisted as
    a list of ISO strings (JSON column, see `models.BusinessCalendar`)."""
    for raw in non_working_dates:
        try:
            date.fromisoformat(raw)
        except ValueError as exc:
            raise InvalidBusinessCalendarError(
                f"{raw!r} ist kein gültiges ISO-Datum (YYYY-MM-DD)"
            ) from exc


async def refresh_business_calendar_cache(session: AsyncSession) -> None:
    """Reloads ALL business calendars into `spiff_adapter`'s in-memory cache
    (P14-S5) - called after every write access, so `business_days()` never
    sees a stale state. Also the entry point for `main.py`'s one-time load
    at service start."""
    result = await session.execute(select(BusinessCalendar))
    calendars = list(result.scalars().all())
    cache = {c.name: {date.fromisoformat(d) for d in c.non_working_dates} for c in calendars}
    default_name = next((c.name for c in calendars if c.is_default), None)
    spiff_adapter.register_business_calendars(cache, default_name=default_name)


async def create_business_calendar(
    session: AsyncSession, *, name: str, non_working_dates: list[str], is_default: bool
) -> BusinessCalendar:
    _parse_non_working_dates(non_working_dates)
    existing = await session.execute(select(BusinessCalendar).where(BusinessCalendar.name == name))
    if existing.scalar_one_or_none() is not None:
        raise DuplicateBusinessCalendarNameError(f"Kalendername {name!r} bereits vergeben")

    if is_default:
        await session.execute(
            update(BusinessCalendar).where(BusinessCalendar.is_default).values(is_default=False)
        )

    now = datetime.now(UTC)
    calendar = BusinessCalendar(
        name=name,
        non_working_dates=non_working_dates,
        is_default=is_default,
        created_at=now,
        updated_at=now,
    )
    session.add(calendar)
    await session.flush()
    await refresh_business_calendar_cache(session)
    return calendar


async def get_business_calendar(
    session: AsyncSession, business_calendar_id: int
) -> BusinessCalendar:
    calendar = await session.get(BusinessCalendar, business_calendar_id)
    if calendar is None:
        raise NotFoundError(f"business_calendar_id {business_calendar_id!r} unbekannt")
    return calendar


async def list_business_calendars(session: AsyncSession) -> list[BusinessCalendar]:
    result = await session.execute(select(BusinessCalendar).order_by(BusinessCalendar.name))
    return list(result.scalars().all())


async def update_business_calendar(
    session: AsyncSession,
    business_calendar_id: int,
    *,
    name: str,
    non_working_dates: list[str],
    is_default: bool,
) -> BusinessCalendar:
    calendar = await get_business_calendar(session, business_calendar_id)
    _parse_non_working_dates(non_working_dates)
    if name != calendar.name:
        existing = await session.execute(
            select(BusinessCalendar).where(BusinessCalendar.name == name)
        )
        if existing.scalar_one_or_none() is not None:
            raise DuplicateBusinessCalendarNameError(f"Kalendername {name!r} bereits vergeben")

    if is_default and not calendar.is_default:
        await session.execute(
            update(BusinessCalendar).where(BusinessCalendar.is_default).values(is_default=False)
        )

    calendar.name = name
    calendar.non_working_dates = non_working_dates
    calendar.is_default = is_default
    calendar.updated_at = datetime.now(UTC)
    await session.flush()
    await refresh_business_calendar_cache(session)
    return calendar


async def delete_business_calendar(session: AsyncSession, business_calendar_id: int) -> None:
    calendar = await get_business_calendar(session, business_calendar_id)
    await session.delete(calendar)
    await session.flush()
    await refresh_business_calendar_cache(session)


async def start_instance(
    session: AsyncSession,
    process_definition_id: int,
    *,
    created_by: str,
    business_key: str | None,
    initial_data: dict,
    instance_id: str | None = None,
    route: list[dict] | None = None,
) -> ProcessInstance:
    """`route` (P93-S3): required for a circulation definition, at least one
    entry (`validate_route_entry`); ignored otherwise."""
    definition = await get_process_definition(session, process_definition_id)
    if definition.is_circulation and not route:
        raise InvalidRouteError(
            "Ein Umlauf braucht mindestens einen Schritt im Laufweg (dms_route)"
        )
    dmn_xmls = await list_latest_dmn_xml(session)
    try:
        spec, _ = spiff_adapter.parse_bpmn(
            definition.bpmn_xml, definition.bpmn_process_id, dmn_definitions=dmn_xmls
        )
    except spiff_adapter.BpmnParseError as exc:
        raise InvalidBpmnError(str(exc)) from exc

    wf = spiff_adapter.new_workflow(spec)
    spiff_adapter.set_initial_data(wf, initial_data)

    now = datetime.now(UTC)
    instance = ProcessInstance(
        id=instance_id or str(uuid.uuid4()),
        process_definition_id=process_definition_id,
        business_key=business_key,
        status="running",
        workflow_state=spiff_adapter.serialize(wf),
        created_by=created_by,
        created_at=now,
        updated_at=now,
        completed_at=None,
    )
    session.add(instance)
    await session.flush()
    if definition.is_circulation:
        for index, entry in enumerate(route or []):
            session.add(
                _new_step(
                    instance.id,
                    index,
                    entry,
                    status="current" if index == 0 else "pending",
                    added_by=created_by,
                    now=now,
                )
            )
        await session.flush()

    # `try`/`finally` instead of a simple sequential flow (P12-S2,
    # resumability, 7.2): if `run_ready_steps()` raises (e.g. a
    # `connector_call` service task whose target was unreachable), the
    # resulting `ERROR` state MUST still be persisted - otherwise there
    # would be no instance row at all with the correct intermediate state
    # for `POST /instances/{id}/retry` to resume. Without this `finally`,
    # the instance would never end up in the DB at all on an exception
    # here (found for real while writing the corresponding API test).
    try:
        spiff_adapter.run_ready_steps(wf)
    finally:
        await _save_state(session, instance, wf)
    return instance


async def get_instance(session: AsyncSession, instance_id: str) -> ProcessInstance:
    instance = await session.get(ProcessInstance, instance_id)
    if instance is None:
        raise NotFoundError(f"instance_id {instance_id!r} unbekannt")
    return instance


async def list_instances(
    session: AsyncSession,
    *,
    process_definition_id: int | None = None,
    status: str | None = None,
    business_key: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[ProcessInstance]:
    """`limit`/`offset` since P62-S1 - `None` (the default) means unbounded,
    deliberately preserved for this function's two INTERNAL callers
    (`advance_timers`'s SLA poll loop, `list_ready_tasks`'s cross-instance
    task scan) which both need every matching instance for correctness, not
    just a page of them. Only `main.py`'s `GET /instances` HTTP endpoint
    passes an explicit `limit`."""
    query = select(ProcessInstance)
    if process_definition_id is not None:
        query = query.where(ProcessInstance.process_definition_id == process_definition_id)
    if status is not None:
        query = query.where(ProcessInstance.status == status)
    if business_key is not None:
        query = query.where(ProcessInstance.business_key == business_key)
    query = query.order_by(ProcessInstance.created_at.desc())
    if limit is not None:
        query = query.limit(limit).offset(offset)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_ready_tasks(session: AsyncSession, instance_id: str) -> list[spiff_adapter.TaskInfo]:
    instance = await get_instance(session, instance_id)
    wf = spiff_adapter.deserialize(instance.workflow_state)
    return spiff_adapter.ready_manual_tasks(wf)


async def complete_task(
    session: AsyncSession, instance_id: str, task_id: str, *, completed_by: str, data: dict
) -> ProcessInstance:
    instance = await get_instance(session, instance_id)
    wf = spiff_adapter.deserialize(instance.workflow_state)
    task = spiff_adapter.find_ready_task(wf, task_id)
    if task is None:
        raise TaskNotReadyError(
            f"task_id {task_id!r} ist bei instance_id {instance_id!r} nicht bereit"
        )

    # Circulation step (P93-S3): the route moves on, and the gateway learns
    # whether another step follows.
    extensions = getattr(task.task_spec, "extensions", None) or {}
    if extensions.get("taskType") == spiff_adapter.CIRCULATION_TASK_TYPE:
        more = await _advance_route(session, instance_id, completed_by)
        data = {**data, "dms_route_more": more}

    # completed_by is deliberately not merged into the process variables
    # (could collide with a BPMN process's own variables) - only used as
    # event payload when publishing in main.py, see there.
    spiff_adapter.complete_task(task, data)
    # `try`/`finally`: see `start_instance` - a subsequent automatic step
    # (e.g. `connector_call`) can fail, and the already-completed Manual
    # Task must not be lost in that case (P12-S2).
    try:
        spiff_adapter.run_ready_steps(wf)
    finally:
        try:
            await _save_state(session, instance, wf)
        except StaleDataError as exc:
            # Optimistic-concurrency conflict (Phase 60 Session 3, ADR
            # 0185) - another request already modified this instance
            # since it was read above (e.g. the sibling of a parallel
            # gateway completed first). The caller (`main.complete_task`)
            # must roll back, not commit, before returning `409`.
            raise ConcurrentModificationError(
                f"instance_id {instance_id!r} wurde gleichzeitig von einer anderen "
                "Anfrage geändert - bitte erneut versuchen"
            ) from exc
    return instance


# --- Administrative instance operations (P93-S2, ADR 0249 Part A c) -------


@dataclass
class ReleasedClaim:
    principal_id: str
    task_id: str
    task_name: str
    delegation_ids: list[str]


@dataclass
class InstanceModification:
    """Outcome of skip / send back / migrate, for the event and the response."""

    instance: ProcessInstance
    target_name: str
    tasks_before: list[str]
    tasks_after: list[str]
    released_claims: list[ReleasedClaim] = field(default_factory=list)


async def _modify_instance(
    session: AsyncSession,
    instance_id: str,
    expected_version: int | None,
    apply: Callable[[object], str],
) -> InstanceModification:
    """Shared read-modify-write for the three operations: `apply` changes the
    deserialized workflow (or raises `InstanceOperationRefused`) and returns
    the name to report. Afterwards automatic steps run, the state is saved
    under the optimistic-concurrency check, and claims on tasks that are no
    longer ready are deleted (their grants are revoked by the caller)."""
    instance = await get_instance(session, instance_id)
    if instance.status != "running":
        raise InstanceNotRunningError(f"instance_id {instance_id!r} ist nicht 'running'")
    if expected_version is not None and instance.workflow_version != expected_version:
        raise ConcurrentModificationError(
            f"instance_id {instance_id!r} wurde inzwischen geändert - bitte neu laden"
        )
    wf = spiff_adapter.deserialize(instance.workflow_state)
    before = spiff_adapter.ready_manual_tasks(wf)
    target_name = apply(wf)
    try:
        spiff_adapter.run_ready_steps(wf)
    finally:
        try:
            await _save_state(session, instance, wf)
        except StaleDataError as exc:
            raise ConcurrentModificationError(
                f"instance_id {instance_id!r} wurde gleichzeitig von einer anderen "
                "Anfrage geändert - bitte erneut versuchen"
            ) from exc
    after = spiff_adapter.ready_manual_tasks(wf)

    after_ids = {t.id for t in after}
    names = {t.id: t.name for t in before}
    released: list[ReleasedClaim] = []
    for claim in await get_task_claims_for_instance(session, instance_id):
        if claim.task_id in after_ids:
            continue
        released.append(
            ReleasedClaim(
                principal_id=claim.principal_id,
                task_id=claim.task_id,
                task_name=names.get(claim.task_id, "?"),
                delegation_ids=list(claim.granted_delegation_ids or []),
            )
        )
        await session.delete(claim)
    try:
        await session.flush()
    except StaleDataError as exc:
        raise ConcurrentModificationError(
            f"instance_id {instance_id!r} wurde gleichzeitig von einer anderen "
            "Anfrage geändert - bitte erneut versuchen"
        ) from exc
    return InstanceModification(
        instance=instance,
        target_name=target_name,
        tasks_before=[t.name for t in before],
        tasks_after=[t.name for t in after],
        released_claims=released,
    )


async def skip_task(
    session: AsyncSession,
    instance_id: str,
    task_id: str,
    *,
    actor: str,
    reason: str,
    expected_version: int | None,
) -> InstanceModification:
    marker = {"by": actor, "reason": reason, "at": datetime.now(UTC).isoformat()}
    return await _modify_instance(
        session,
        instance_id,
        expected_version,
        lambda wf: spiff_adapter.skip_task(wf, task_id, marker),
    )


async def completed_tasks(
    session: AsyncSession, instance_id: str
) -> list[spiff_adapter.CompletedTaskInfo]:
    instance = await get_instance(session, instance_id)
    return spiff_adapter.completed_manual_tasks(spiff_adapter.deserialize(instance.workflow_state))


async def send_back(
    session: AsyncSession, instance_id: str, target_task_id: str, *, expected_version: int | None
) -> InstanceModification:
    return await _modify_instance(
        session,
        instance_id,
        expected_version,
        lambda wf: spiff_adapter.send_back(wf, target_task_id)[0],
    )


async def _migration_target(
    session: AsyncSession, instance: ProcessInstance, target_definition_id: int
) -> tuple[ProcessDefinition, object]:
    """The target must be a newer version of the instance's own process
    family; returns it with its parsed spec."""
    current = await get_process_definition(session, instance.process_definition_id)
    target = await get_process_definition(session, target_definition_id)
    if target.name != current.name or target.version <= current.version:
        raise spiff_adapter.InstanceOperationRefused(
            f"Ziel muss eine neuere Version von {current.name!r} sein "
            f"(aktuell Version {current.version})"
        )
    dmn_xmls = await list_latest_dmn_xml(session)
    try:
        spec, _ = spiff_adapter.parse_bpmn(
            target.bpmn_xml, target.bpmn_process_id, dmn_definitions=dmn_xmls
        )
    except spiff_adapter.BpmnParseError as exc:
        raise InvalidBpmnError(str(exc)) from exc
    return target, spec


async def migration_preview(
    session: AsyncSession, instance_id: str, target_definition_id: int
) -> spiff_adapter.MigrationReport:
    instance = await get_instance(session, instance_id)
    if instance.status != "running":
        raise InstanceNotRunningError(f"instance_id {instance_id!r} ist nicht 'running'")
    _target, spec = await _migration_target(session, instance, target_definition_id)
    return spiff_adapter.migration_report(spiff_adapter.deserialize(instance.workflow_state), spec)


async def migrate_instance(
    session: AsyncSession,
    instance_id: str,
    target_definition_id: int,
    *,
    expected_version: int | None,
) -> InstanceModification:
    instance = await get_instance(session, instance_id)
    target, spec = await _migration_target(session, instance, target_definition_id)

    def apply(wf) -> str:
        # Re-checked on the state actually being changed, not trusted from
        # an earlier preview.
        report = spiff_adapter.migration_report(wf, spec)
        if not report.possible:
            raise spiff_adapter.InstanceOperationRefused("; ".join(report.reasons))
        spiff_adapter.migrate(wf, spec)
        # Same identity-mapped row `_modify_instance` saves - one flush, one
        # version bump.
        instance.process_definition_id = target.id
        return f"{target.name} v{target.version}"

    return await _modify_instance(session, instance_id, expected_version, apply)


# --- Circulation routes (P93-S3, ADR 0249 Part A b) ------------------------

CIRCULATION_TEMPLATE_NAME = "Umlauf"
ROUTE_KINDS = ("mitzeichnen", "zeichnen", "kenntnis", "bearbeiten")
ASSIGNEE_TYPES = ("principal", "group")
_OPEN_STATUSES = ("current", "pending")


class InvalidRouteError(Exception):
    """A route entry is malformed, or a circulation started without one."""


class RouteChangeRefused(Exception):
    """A route change that would touch a finished step, or has no target."""


async def ensure_circulation_template(session: AsyncSession) -> ProcessDefinition:
    """Seeds the shipped "Umlauf" template once (idempotent: an existing
    family of that name, also an edited one, is left alone)."""
    existing = await list_process_definitions(session, name=CIRCULATION_TEMPLATE_NAME)
    if existing:
        return existing[0]
    return await create_process_definition(
        session,
        name=CIRCULATION_TEMPLATE_NAME,
        bpmn_xml=spiff_adapter.circulation_template_xml(),
        process_id=None,
    )


def validate_route_entry(entry: dict) -> dict:
    """Normalises one route entry `{kind, assignee_type, assignee, note?}`."""
    if not isinstance(entry, dict):
        raise InvalidRouteError("Ein Schritt im Laufweg muss ein Objekt sein")
    kind = entry.get("kind")
    assignee_type = entry.get("assignee_type")
    assignee = str(entry.get("assignee") or "").strip()
    note = entry.get("note")
    if kind not in ROUTE_KINDS:
        allowed = ", ".join(ROUTE_KINDS)
        raise InvalidRouteError(f"Unbekannte Schrittart {kind!r} (erlaubt: {allowed})")
    if assignee_type not in ASSIGNEE_TYPES:
        raise InvalidRouteError("assignee_type muss 'principal' oder 'group' sein")
    if not assignee or len(assignee) > 256:
        raise InvalidRouteError("Jeder Schritt braucht eine zuständige Person oder Gruppe")
    if note is not None and (not isinstance(note, str) or len(note) > 2000):
        raise InvalidRouteError("Die Notiz eines Schritts ist zu lang")
    return {
        "kind": kind,
        "assignee_type": assignee_type,
        "assignee": assignee,
        "note": (note or "").strip() or None,
    }


def _new_step(
    instance_id: str, position: int, entry: dict, *, status: str, added_by: str, now: datetime
) -> CirculationStep:
    entry = validate_route_entry(entry)
    return CirculationStep(
        id=str(uuid.uuid4()),
        instance_id=instance_id,
        position=position,
        status=status,
        added_by=added_by,
        added_at=now,
        **entry,
    )


async def get_route(session: AsyncSession, instance_id: str) -> list[CirculationStep]:
    result = await session.execute(
        select(CirculationStep)
        .where(CirculationStep.instance_id == instance_id)
        .order_by(CirculationStep.position, CirculationStep.added_at)
    )
    return list(result.scalars().all())


async def current_step(session: AsyncSession, instance_id: str) -> CirculationStep | None:
    return next((s for s in await get_route(session, instance_id) if s.status == "current"), None)


async def _advance_route(session: AsyncSession, instance_id: str, completed_by: str) -> bool:
    """The current step is done; the next pending one becomes current.
    Returns whether there is one."""
    steps = await get_route(session, instance_id)
    now = datetime.now(UTC)
    for step in steps:
        if step.status == "current":
            step.status = "done"
            step.closed_by = completed_by
            step.closed_at = now
    following = next((s for s in steps if s.status == "pending"), None)
    if following is not None:
        following.status = "current"
    await session.flush()
    return following is not None


@dataclass
class RouteChange:
    instance: ProcessInstance
    description: str
    inserted: list[CirculationStep] = field(default_factory=list)
    released_claims: list[ReleasedClaim] = field(default_factory=list)


async def _change_route(
    session: AsyncSession,
    instance_id: str,
    expected_version: int | None,
    mutate: Callable[[list[CirculationStep]], tuple[list[CirculationStep], RouteChange]],
) -> RouteChange:
    """Shared frame for route edits: the instance must be a running
    circulation at `expected_version`; `mutate` returns the route in its new
    order (finished steps untouched) and what changed. Positions are
    renumbered, and the instance row is touched so its version moves on -
    a concurrent edit or step completion then conflicts (ADR 0185)."""
    instance = await get_instance(session, instance_id)
    if instance.status != "running":
        raise InstanceNotRunningError(f"instance_id {instance_id!r} ist nicht 'running'")
    if expected_version is not None and instance.workflow_version != expected_version:
        raise ConcurrentModificationError(
            f"instance_id {instance_id!r} wurde inzwischen geändert - bitte neu laden"
        )
    steps = await get_route(session, instance_id)
    if not steps:
        raise RouteChangeRefused("Diese Instanz ist kein Umlauf")
    ordered, change = mutate(steps)
    for index, step in enumerate(ordered):
        step.position = index
    for step in change.inserted:
        session.add(step)
    instance.updated_at = datetime.now(UTC)
    change.instance = instance
    try:
        await session.flush()
    except StaleDataError as exc:
        raise ConcurrentModificationError(
            f"instance_id {instance_id!r} wurde gleichzeitig geändert - bitte neu laden"
        ) from exc
    return change


def _pending(steps: list[CirculationStep]) -> list[CirculationStep]:
    return [s for s in steps if s.status == "pending"]


def _describe_step(step: CirculationStep) -> str:
    return f"{step.kind} ({step.assignee})"


async def add_route_step(
    session: AsyncSession,
    instance_id: str,
    entry: dict,
    *,
    index: int | None,
    actor: str,
    expected_version: int | None,
) -> RouteChange:
    """Inserts a step among the open future ones: `index` 0 = right after the
    current step, None = at the end."""

    def mutate(steps):
        step = _new_step(
            instance_id, 0, entry, status="pending", added_by=actor, now=datetime.now(UTC)
        )
        pending = _pending(steps)
        ordered = list(steps)
        if index is None or index >= len(pending):
            ordered.append(step)
        else:
            if index < 0:
                raise RouteChangeRefused("Ungültige Position")
            ordered.insert(ordered.index(pending[index]), step)
        return ordered, RouteChange(
            instance=None, description=f"Schritt ergänzt: {_describe_step(step)}", inserted=[step]
        )

    return await _change_route(session, instance_id, expected_version, mutate)


async def remove_route_step(
    session: AsyncSession,
    instance_id: str,
    step_id: str,
    *,
    actor: str,
    reason: str,
    expected_version: int | None,
) -> RouteChange:
    """Strikes a future step. A finished step can't be changed; the current
    one is recalled instead."""

    def mutate(steps):
        step = next((s for s in steps if s.id == step_id), None)
        if step is None:
            raise NotFoundError(f"Schritt {step_id!r} gehört nicht zu diesem Umlauf")
        if step.status != "pending":
            raise RouteChangeRefused(
                "Nur künftige Schritte können gestrichen werden - der aktuelle wird zurückgeholt"
            )
        step.status = "removed"
        step.closed_by = actor
        step.closed_at = datetime.now(UTC)
        step.close_reason = reason
        return steps, RouteChange(
            instance=None, description=f"Schritt gestrichen: {_describe_step(step)}"
        )

    return await _change_route(session, instance_id, expected_version, mutate)


async def reorder_route(
    session: AsyncSession,
    instance_id: str,
    step_ids: list[str],
    *,
    expected_version: int | None,
) -> RouteChange:
    """New order of the future steps; `step_ids` must name exactly them."""

    def mutate(steps):
        pending = _pending(steps)
        if sorted(step_ids) != sorted(s.id for s in pending) or len(set(step_ids)) != len(step_ids):
            raise RouteChangeRefused(
                "Die neue Reihenfolge muss genau die künftigen Schritte enthalten"
            )
        by_id = {s.id: s for s in pending}
        queue = iter(by_id[i] for i in step_ids)
        ordered = [next(queue) if s.status == "pending" else s for s in steps]
        return ordered, RouteChange(instance=None, description="Reihenfolge geändert")

    return await _change_route(session, instance_id, expected_version, mutate)


async def recall_route_step(
    session: AsyncSession,
    instance_id: str,
    redirect: dict,
    *,
    actor: str,
    reason: str,
    expected_version: int | None,
) -> RouteChange:
    """Takes the current step back and gives it to someone else (`redirect`,
    a route entry - e.g. back to the author). The ready task stays the same
    engine task; its claim is released. The remaining future steps follow
    unchanged."""
    ready = await get_ready_tasks(session, instance_id)
    task = next(
        (t for t in ready if t.extensions.get("taskType") == spiff_adapter.CIRCULATION_TASK_TYPE),
        None,
    )
    claim = await get_task_claim(session, instance_id, task.id) if task else None

    def mutate(steps):
        current = next((s for s in steps if s.status == "current"), None)
        if current is None or task is None:
            raise RouteChangeRefused(
                "Es gibt keinen laufenden Schritt, der zurückgeholt werden kann"
            )
        now = datetime.now(UTC)
        current.status = "recalled"
        current.closed_by = actor
        current.closed_at = now
        current.close_reason = reason
        step = _new_step(instance_id, 0, redirect, status="current", added_by=actor, now=now)
        ordered = list(steps)
        ordered.insert(ordered.index(current) + 1, step)
        change = RouteChange(
            instance=None,
            description=f"Zurückgeholt: {_describe_step(current)} -> {_describe_step(step)}",
            inserted=[step],
        )
        if claim is not None:
            change.released_claims.append(
                ReleasedClaim(
                    principal_id=claim.principal_id,
                    task_id=claim.task_id,
                    task_name=task.name,
                    delegation_ids=list(claim.granted_delegation_ids or []),
                )
            )
        return ordered, change

    change = await _change_route(session, instance_id, expected_version, mutate)
    if claim is not None:
        await session.delete(claim)
        await session.flush()
    return change


async def get_task_claim(session: AsyncSession, instance_id: str, task_id: str) -> TaskClaim | None:
    result = await session.execute(
        select(TaskClaim).where(TaskClaim.instance_id == instance_id, TaskClaim.task_id == task_id)
    )
    return result.scalars().first()


async def get_task_claims_for_instance(session: AsyncSession, instance_id: str) -> list[TaskClaim]:
    """Basis for enriching a task listing with `claimed_by`/`grant_kind`
    (Post-Roadmap Phase 31 Session 10) - one query per instance instead of
    per task, mirroring `get_ready_tasks`' own per-instance shape."""
    result = await session.execute(select(TaskClaim).where(TaskClaim.instance_id == instance_id))
    return list(result.scalars().all())


async def claim_task(
    session: AsyncSession, instance_id: str, task_id: str, principal_id: str
) -> TaskClaim:
    """Minimal task-claim mechanism (Post-Roadmap Phase 31 Session 10, see
    `models.TaskClaim`). Idempotent if the SAME principal claims again
    (matches `permission_service.add_group_member`'s precedent); a
    DIFFERENT principal attempting to claim an already-claimed task gets
    `TaskAlreadyClaimedError` - claiming is exclusive."""
    tasks = await get_ready_tasks(session, instance_id)
    if not any(t.id == task_id for t in tasks):
        raise TaskNotReadyError(
            f"task_id {task_id!r} ist bei instance_id {instance_id!r} nicht bereit"
        )
    existing = await get_task_claim(session, instance_id, task_id)
    if existing is not None:
        if existing.principal_id != principal_id:
            raise TaskAlreadyClaimedError(
                f"task_id {task_id!r} ist bereits von {existing.principal_id!r} beansprucht"
            )
        return existing
    claim = TaskClaim(
        instance_id=instance_id,
        task_id=task_id,
        principal_id=principal_id,
        claimed_at=datetime.now(UTC),
    )
    session.add(claim)
    await session.flush()
    return claim


async def set_claim_grant(
    session: AsyncSession, claim: TaskClaim, *, grant_kind: str, delegation_ids: list[str]
) -> TaskClaim:
    """Records which `Delegation` IDs (permission-service) were auto-created
    for this claim's org-hierarchy access grant (Post-Roadmap Phase 31
    Session 10), so `release_task_claim`/task completion can revoke exactly
    those again. A second grant request for the same claim REPLACES the
    previous one rather than accumulating - "the" active grant for a claim
    is singular at any moment, matching the concept wording ("a workflow
    task can grant ... temporary access", not "grants")."""
    claim.grant_kind = grant_kind
    claim.granted_delegation_ids = delegation_ids
    await session.flush()
    return claim


async def release_task_claim(session: AsyncSession, instance_id: str, task_id: str) -> list[str]:
    """Releases a claim and returns the `Delegation` IDs (if any) that were
    granted for it, so the caller (`main.py`) can revoke them at
    permission-service BEFORE they're gone from the DB - hard-deleted, not
    soft-released, same precedent as `document-service`'s `DocumentLock`
    force-unlock (ADR 0002: "deletes the lock entirely rather than moving
    it into a third state")."""
    claim = await get_task_claim(session, instance_id, task_id)
    if claim is None:
        raise NotFoundError(
            f"Kein aktiver Claim für task_id {task_id!r} bei instance_id {instance_id!r}"
        )
    delegation_ids = list(claim.granted_delegation_ids or [])
    await session.delete(claim)
    await session.flush()
    return delegation_ids


async def reassign_task_claim(
    session: AsyncSession, instance_id: str, task_id: str, new_principal_id: str
) -> tuple[TaskClaim, list[str]]:
    """Reassignment (Post-Roadmap Phase 35 Session 3, ADR 0145) - the
    feature ADR 0121 explicitly scoped out ("no reassignment"). Requires an
    EXISTING claim (`404` otherwise, same as `create_task_org_hierarchy_grant`
    - there is nothing to reassign FROM without one; a genuinely unclaimed
    task is claimed directly via the existing `POST .../claim`, not
    "reassigned"). Deletes the old row and inserts a fresh one rather than
    updating `principal_id` in place - the new claim starts with a clean
    `claimed_at`/`expiry_notified_at` (a reassigned task's new owner gets
    their own full abandonment window, not the old owner's remaining time),
    and `grant_kind`/`granted_delegation_ids` are NOT carried over (an
    org-hierarchy grant was resolved FROM the old assignee, it makes no
    sense for the new one - the caller must request a fresh one if needed).
    Returns the new claim AND the old claim's `Delegation` IDs, so the
    caller can revoke them at permission-service exactly like
    `release_task_claim` already does."""
    old_claim = await get_task_claim(session, instance_id, task_id)
    if old_claim is None:
        raise NotFoundError(
            f"Kein aktiver Claim für task_id {task_id!r} bei instance_id {instance_id!r} - "
            "ein unbeanspruchter Task wird direkt beansprucht, nicht neu zugewiesen"
        )
    delegation_ids = list(old_claim.granted_delegation_ids or [])
    await session.delete(old_claim)
    await session.flush()
    new_claim = TaskClaim(
        instance_id=instance_id,
        task_id=task_id,
        principal_id=new_principal_id,
        claimed_at=datetime.now(UTC),
    )
    session.add(new_claim)
    await session.flush()
    return new_claim, delegation_ids


async def list_claims_due_for_expiry_notice(
    session: AsyncSession, *, threshold_hours: float
) -> list[TaskClaim]:
    """Claim-abandonment notification (Post-Roadmap Phase 35 Session 3, ADR
    0145) - same "sent once per current deadline" dedup shape as
    `document_service.repository.list_locks_due_for_reminder`, just keyed
    on `claimed_at` (reset to a fresh value by `reassign_task_claim` above,
    so a reassigned task's new owner starts a clean abandonment window)
    instead of `DocumentLock.locked_at`."""
    threshold = datetime.now(UTC) - timedelta(hours=threshold_hours)
    result = await session.execute(
        select(TaskClaim).where(
            TaskClaim.claimed_at <= threshold,
            TaskClaim.expiry_notified_at.is_(None),
        )
    )
    return list(result.scalars().all())


async def retry_instance(session: AsyncSession, instance_id: str) -> ProcessInstance:
    """Resumability for a failed automatic step (7.2, P12-S2) - a generic
    primitive, not migration-specific: resets `ERROR` tasks
    (`spiff_adapter.retry_errored_tasks`) and then tries again to advance
    the workflow. Persists the new intermediate state even on a repeated
    failure (`try`/`finally`, see `start_instance`) - otherwise the
    instance would stay stuck at the original failure point for a third
    `retry` attempt, instead of at the actually last (possibly again
    failed) state."""
    instance = await get_instance(session, instance_id)
    if instance.status != "running":
        raise InstanceNotRunningError(f"instance_id {instance_id!r} ist nicht 'running'")
    wf = spiff_adapter.deserialize(instance.workflow_state)
    spiff_adapter.retry_errored_tasks(wf)

    try:
        spiff_adapter.run_ready_steps(wf)
    finally:
        try:
            await _save_state(session, instance, wf)
        except StaleDataError as exc:
            # Same optimistic-concurrency conflict as `complete_task`
            # above (Phase 60 Session 3, ADR 0185) - e.g. racing against a
            # concurrent `complete_task` on the same instance, or against
            # the SLA poll loop's own `advance_timers` tick.
            raise ConcurrentModificationError(
                f"instance_id {instance_id!r} wurde gleichzeitig von einer anderen "
                "Anfrage geändert - bitte erneut versuchen"
            ) from exc
    return instance


async def advance_timers(session: AsyncSession) -> list[TimerAdvanceResult]:
    """A poll tick of SLA time monitoring (P6-S2, ADR 0020): deserializes
    every running instance that waits on a timer, lets due boundary timers
    fire, and re-persists the blob - regardless of whether anything fired,
    since the internal timer state (next due time) can also change
    otherwise. Until P95-S1b this was every running instance; now
    `ProcessInstance.waits_on_timer` (kept by `_save_state`) narrows it to
    those with a waiting timer, `None` (not yet indexed) counting as yes.

    Since Phase 60 Session 3 (ADR 0185), `ProcessInstance.workflow_version`
    protects this batched write too - if ANY instance in this tick's batch
    lost a race against a concurrent `complete_task`/`retry_instance`
    since `list_instances` read it above, the single `session.flush()`
    below raises `StaleDataError`, which is deliberately left
    UNTRANSLATED here (unlike `complete_task`/`retry_instance`'s own
    `ConcurrentModificationError` wrapping) - it propagates to `main.
    _sla_poll_loop`'s existing `except Exception` catch, which ALREADY
    tolerates "a single broken blob" failing the WHOLE tick and retrying
    on the next one (see that function's own docstring) - the same
    accepted tolerance model, not a new one. A per-instance-continue
    restructure (so one conflicted instance doesn't also roll back
    everyone else's otherwise-successful timer advances in the same tick)
    was considered and explicitly deferred: SLA ticks repeat every
    `sla_poll_interval_seconds`, so the cost of losing one whole tick to a
    rare race is low, and the restructure would need per-instance
    transactions/savepoints - real added complexity for a narrow edge
    case, not undertaken in this session."""
    result = await session.execute(
        select(ProcessInstance)
        .where(ProcessInstance.status == "running")
        .where(ProcessInstance.waits_on_timer.is_not(False))
        .order_by(ProcessInstance.created_at.desc())
    )
    results: list[TimerAdvanceResult] = []
    for instance in result.scalars().all():
        wf = spiff_adapter.deserialize(instance.workflow_state)
        fired = spiff_adapter.check_timers(wf)
        completed = await _save_state(session, instance, wf)
        results.append(
            TimerAdvanceResult(instance=instance, fired=fired, newly_completed=completed)
        )
    return results


# --- Ready-task index (P95-S1b) ---------------------------------------------


def _json_default(value: object) -> str:
    # Task data may hold dates and other values the engine's own serializer
    # handles; the index stores what a JSON response would show.
    if isinstance(value, date | datetime):
        return value.isoformat()
    return str(value)


def _json_safe(value: dict) -> dict:
    return json.loads(json.dumps(value, default=_json_default))


async def _save_state(session: AsyncSession, instance: ProcessInstance, wf) -> bool:
    """The one place an instance's engine state is written back (P95-S1b):
    serializes the workflow, sets status and timestamps, records whether a
    timer waits, flushes (the optimistic-concurrency check of ADR 0185
    fires here, before any index row is touched), then brings the instance's
    `ReadyTask` rows in line with the engine's ready user tasks. Returns
    whether the instance completed. Raises `StaleDataError` on a conflict -
    the callers translate it as before."""
    completed = spiff_adapter.is_completed(wf)
    now = datetime.now(UTC)
    instance.workflow_state = spiff_adapter.serialize(wf)
    instance.status = "completed" if completed else "running"
    instance.updated_at = now
    if completed:
        instance.completed_at = now
    instance.waits_on_timer = not completed and spiff_adapter.waits_on_timer(wf)
    await session.flush()
    ready = [] if completed else spiff_adapter.ready_manual_tasks(wf)
    await _sync_ready_tasks(session, instance, ready, now)
    return completed


async def _sync_ready_tasks(
    session: AsyncSession,
    instance: ProcessInstance,
    ready: list[spiff_adapter.TaskInfo],
    now: datetime,
) -> None:
    """Rows for tasks that are no longer ready go, new ones come, and the
    rest are updated in place (their `ready_since` stays)."""
    result = await session.execute(select(ReadyTask).where(ReadyTask.instance_id == instance.id))
    existing = {row.task_id: row for row in result.scalars().all()}
    ready_ids = {task.id for task in ready}
    for task_id, row in existing.items():
        if task_id not in ready_ids:
            await session.delete(row)
    for position, task in enumerate(ready):
        row = existing.get(task.id)
        if row is None:
            row = ReadyTask(
                instance_id=instance.id,
                task_id=task.id,
                ready_since=now,
                instance_created_at=instance.created_at,
            )
            session.add(row)
        row.position = position
        row.name = task.name
        row.lane = task.lane
        row.task_type = task.extensions.get("taskType")
        row.extensions = _json_safe(task.extensions)
        row.data = _json_safe(task.data)
    await session.flush()


def _task_info(row: ReadyTask) -> spiff_adapter.TaskInfo:
    return spiff_adapter.TaskInfo(
        id=row.task_id,
        name=row.name,
        lane=row.lane,
        data=dict(row.data or {}),
        extensions=dict(row.extensions or {}),
    )


async def indexed_ready_tasks(
    session: AsyncSession, instance_id: str
) -> list[spiff_adapter.TaskInfo]:
    """An instance's ready user tasks from the index - no deserialization.
    The write paths keep using `get_ready_tasks` (the blob is the truth)."""
    result = await session.execute(
        select(ReadyTask).where(ReadyTask.instance_id == instance_id).order_by(ReadyTask.position)
    )
    return [_task_info(row) for row in result.scalars().all()]


@dataclass
class IndexedTask:
    task: spiff_adapter.TaskInfo
    instance: ProcessInstance
    claim: TaskClaim | None


async def list_indexed_ready_tasks(
    session: AsyncSession,
    *,
    exclude_task_types: tuple[str, ...] = (),
    claimable_by: str | None = None,
    limit: int,
    offset: int = 0,
) -> list[IndexedTask]:
    """Ready user tasks across all running instances, newest instance first,
    then in the engine's order - one query, paged in SQL. `claimable_by`
    keeps only tasks that are unclaimed or claimed by that principal (the
    part of "Meine Aufgaben" SQL can answer; lane and route eligibility are
    checked by the caller). The instance comes without its state blob."""
    query = (
        select(ReadyTask, ProcessInstance, TaskClaim)
        .join(ProcessInstance, ProcessInstance.id == ReadyTask.instance_id)
        .outerjoin(
            TaskClaim,
            and_(
                TaskClaim.instance_id == ReadyTask.instance_id,
                TaskClaim.task_id == ReadyTask.task_id,
            ),
        )
        .where(ProcessInstance.status == "running")
        .options(defer(ProcessInstance.workflow_state))
    )
    if exclude_task_types:
        query = query.where(
            or_(ReadyTask.task_type.is_(None), ReadyTask.task_type.not_in(exclude_task_types))
        )
    if claimable_by is not None:
        query = query.where(or_(TaskClaim.id.is_(None), TaskClaim.principal_id == claimable_by))
    query = (
        query.order_by(
            ReadyTask.instance_created_at.desc(), ReadyTask.instance_id, ReadyTask.position
        )
        .limit(limit)
        .offset(offset)
    )
    result = await session.execute(query)
    return [
        IndexedTask(task=_task_info(row), instance=instance, claim=claim)
        for row, instance, claim in result.all()
    ]


async def current_steps(
    session: AsyncSession, instance_ids: list[str]
) -> dict[str, list[CirculationStep]]:
    """The routes of several instances in one query (for a page of tasks)."""
    if not instance_ids:
        return {}
    result = await session.execute(
        select(CirculationStep)
        .where(CirculationStep.instance_id.in_(instance_ids))
        .order_by(CirculationStep.instance_id, CirculationStep.position, CirculationStep.added_at)
    )
    routes: dict[str, list[CirculationStep]] = {}
    for step in result.scalars().all():
        routes.setdefault(step.instance_id, []).append(step)
    return routes


async def backfill_ready_task_index(
    session_factory: async_sessionmaker[AsyncSession], *, batch_size: int = 200
) -> int:
    """Indexes the running instances saved before P95-S1b (those with
    `waits_on_timer IS NULL`), once, at startup. Batches are taken with
    `FOR UPDATE SKIP LOCKED`, so several replicas starting together share
    the work. The flag is set with a plain UPDATE so `workflow_version`
    doesn't move (a client's expected version stays valid). A blob that
    can't be read is flagged as waiting on a timer - the SLA loop keeps
    looking at it, as before - and gets no index rows."""
    indexed = 0
    while True:
        async with session_factory() as session:
            result = await session.execute(
                select(ProcessInstance)
                .where(ProcessInstance.status == "running")
                .where(ProcessInstance.waits_on_timer.is_(None))
                .limit(batch_size)
                .with_for_update(skip_locked=True)
            )
            batch = list(result.scalars().all())
            if not batch:
                return indexed
            now = datetime.now(UTC)
            for instance in batch:
                try:
                    wf = spiff_adapter.deserialize(instance.workflow_state)
                    waits = spiff_adapter.waits_on_timer(wf)
                    ready = spiff_adapter.ready_manual_tasks(wf)
                except Exception:  # noqa: BLE001 - one broken blob must not stop startup
                    waits, ready = True, []
                await session.execute(
                    update(ProcessInstance)
                    .where(ProcessInstance.id == instance.id)
                    .values(waits_on_timer=waits)
                    .execution_options(synchronize_session=False)
                )
                await _sync_ready_tasks(session, instance, ready, instance.updated_at or now)
                indexed += 1
            await session.commit()


async def create_federation_task(
    session: AsyncSession,
    *,
    process_instance_id: str | None,
    task_id: str | None,
    handover_id: str,
    direction: str,
    origin_installation_id: str | None,
    status: str,
) -> FederationTask:
    """Link to a handover running on the Federation Hub (7.4, P6-S9) - see
    `models.FederationTask`. A `unique` index on `handover_id` prevents the
    same handover from being accidentally linked twice."""
    now = datetime.now(UTC)
    federation_task = FederationTask(
        process_instance_id=process_instance_id,
        task_id=task_id,
        handover_id=handover_id,
        direction=direction,
        origin_installation_id=origin_installation_id,
        status=status,
        created_at=now,
        updated_at=now,
    )
    session.add(federation_task)
    await session.flush()
    return federation_task


async def get_federation_task_by_task(
    session: AsyncSession, process_instance_id: str, task_id: str
) -> FederationTask | None:
    result = await session.execute(
        select(FederationTask).where(
            FederationTask.process_instance_id == process_instance_id,
            FederationTask.task_id == task_id,
        )
    )
    return result.scalar_one_or_none()


async def get_federation_task_by_handover(
    session: AsyncSession, handover_id: str, *, direction: str = "outbound"
) -> FederationTask | None:
    """Default `direction="outbound"`: `POST /federation/inbound-result`
    always reports back the result of a self-initiated (outbound) handover.
    Without the direction filter, the row would not be unique in the
    self-loopback smoke test (the same `handover_id` exists there as both
    an outbound and an inbound row, see `models.FederationTask`)."""
    result = await session.execute(
        select(FederationTask).where(
            FederationTask.handover_id == handover_id, FederationTask.direction == direction
        )
    )
    return result.scalar_one_or_none()


async def get_inbound_federation_task_for_instance(
    session: AsyncSession, process_instance_id: str
) -> FederationTask | None:
    """Finds the inbound handover that triggered this instance (started via
    `POST /federation/inbound`) - the basis for which
    `origin_installation_id`/`handover_id` a `federated_return` task in
    this instance sends its result back to. Deliberately simplified to
    exactly one inbound handover per instance (see
    docs/services/workflow-service.md "Open Points")."""
    result = await session.execute(
        select(FederationTask).where(
            FederationTask.process_instance_id == process_instance_id,
            FederationTask.direction == "inbound",
        )
    )
    return result.scalar_one_or_none()


async def update_federation_task_status(
    session: AsyncSession, federation_task: FederationTask, status: str
) -> None:
    federation_task.status = status
    federation_task.updated_at = datetime.now(UTC)
    await session.flush()


async def mark_inbound_federation_task_returned(
    session: AsyncSession, federation_task: FederationTask, *, task_id: str, status: str
) -> None:
    """Retroactively links the previously task-less inbound `FederationTask`
    entry (see `get_inbound_federation_task_for_instance`) with the
    `federated_return` task that actually sent the result back - afterwards
    serves as a dispatch lock against a duplicate send-back
    (`get_federation_task_by_task` now finds the row via `task_id`)."""
    federation_task.task_id = task_id
    federation_task.status = status
    federation_task.updated_at = datetime.now(UTC)
    await session.flush()
