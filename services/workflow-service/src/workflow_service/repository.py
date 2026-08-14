import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from workflow_service import spiff_adapter
from workflow_service.models import (
    BusinessCalendar,
    DmnDefinition,
    FederationTask,
    ProcessDefinition,
    ProcessInstance,
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


class InstanceNotRunningError(Exception):
    """`POST /instances/{id}/retry` (P12-S2) on an already `completed`
    instance - nothing to retry."""


# Postgres advisory lock namespaces (P25-S1, ADR 0096) for
# `create_process_definition`/`create_dmn_definition` - two fixed `key1`
# values for the two-integer variant of `pg_advisory_xact_lock(key1, key2)`,
# so that a process definition and a DMN definition that happen to share
# the same `name` do NOT lock each other out (both families are versioned
# independently, see `models.py`). `key2` is `hashtext(name)` in each case -
# arbitrary, but deterministic per family name.
_PROCESS_DEFINITION_LOCK_NAMESPACE = 1
_DMN_DEFINITION_LOCK_NAMESPACE = 2


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
        _, resolved_process_id = spiff_adapter.parse_bpmn(
            bpmn_xml, process_id, dmn_definitions=dmn_xmls
        )
    except spiff_adapter.BpmnParseError as exc:
        raise InvalidBpmnError(str(exc)) from exc

    now = datetime.now(UTC)
    definition = ProcessDefinition(
        name=name,
        version=next_version,
        bpmn_process_id=resolved_process_id,
        bpmn_xml=bpmn_xml,
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
    instead of `SELECT ... FOR UPDATE` on a counter row."""
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
    """Deliberately NO "in use" check (unlike `delete_process_definition`) -
    that would require searching all BPMN XML texts for
    `camunda:decisionRef` to determine whether a `businessRuleTask`
    references this family. A documented, deliberate limitation of this
    reference implementation (see docs/services/workflow-service.md) - a
    deletion can cause an existing process definition to fail at the next
    instance start with a SpiffWorkflow `ValidationException` (translated:
    `InvalidBpmnError`); already-running instances are unaffected (their
    `workflow_state` already fully contains the decision loaded at start
    time)."""
    definition = await get_dmn_definition(session, dmn_definition_id)
    await session.delete(definition)
    await session.flush()


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
) -> ProcessInstance:
    definition = await get_process_definition(session, process_definition_id)
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
        completed = spiff_adapter.is_completed(wf)
        instance.workflow_state = spiff_adapter.serialize(wf)
        instance.status = "completed" if completed else "running"
        instance.updated_at = datetime.now(UTC)
        if completed:
            instance.completed_at = instance.updated_at
        await session.flush()
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
) -> list[ProcessInstance]:
    query = select(ProcessInstance)
    if process_definition_id is not None:
        query = query.where(ProcessInstance.process_definition_id == process_definition_id)
    if status is not None:
        query = query.where(ProcessInstance.status == status)
    if business_key is not None:
        query = query.where(ProcessInstance.business_key == business_key)
    result = await session.execute(query.order_by(ProcessInstance.created_at.desc()))
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
        completed = spiff_adapter.is_completed(wf)
        now = datetime.now(UTC)
        instance.workflow_state = spiff_adapter.serialize(wf)
        instance.status = "completed" if completed else "running"
        instance.updated_at = now
        if completed:
            instance.completed_at = now
        await session.flush()
    return instance


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
        completed = spiff_adapter.is_completed(wf)
        now = datetime.now(UTC)
        instance.workflow_state = spiff_adapter.serialize(wf)
        instance.status = "completed" if completed else "running"
        instance.updated_at = now
        if completed:
            instance.completed_at = now
        await session.flush()
    return instance


async def advance_timers(session: AsyncSession) -> list[TimerAdvanceResult]:
    """A poll tick of SLA time monitoring (P6-S2, ADR 0020): deserializes
    **every** running instance, lets due boundary timers fire, and
    re-persists the blob - regardless of whether anything fired, since the
    internal timer state (next due time) can also change otherwise. The
    consequence already documented in ADR 0019 (no efficient cross-instance
    query possible) applies unchanged here."""
    running = await list_instances(session, status="running")
    results: list[TimerAdvanceResult] = []
    now = datetime.now(UTC)
    for instance in running:
        wf = spiff_adapter.deserialize(instance.workflow_state)
        fired = spiff_adapter.check_timers(wf)
        completed = spiff_adapter.is_completed(wf)
        instance.workflow_state = spiff_adapter.serialize(wf)
        instance.updated_at = now
        if completed:
            instance.status = "completed"
            instance.completed_at = now
        results.append(
            TimerAdvanceResult(instance=instance, fired=fired, newly_completed=completed)
        )
    await session.flush()
    return results


async def create_federation_task(
    session: AsyncSession,
    *,
    process_instance_id: str,
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
