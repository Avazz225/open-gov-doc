import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from workflow_service import spiff_adapter
from workflow_service.models import FederationTask, ProcessDefinition, ProcessInstance


@dataclass
class TimerAdvanceResult:
    """Ergebnis eines Poll-Ticks der SLA-Zeitüberwachung (P6-S2) für eine einzelne
    Instanz - `main.py` übersetzt daraus `workflow.task.escalated`-/
    `workflow.instance.completed`-Events, ohne selbst SpiffWorkflow-Typen zu kennen."""

    instance: ProcessInstance
    fired: list[spiff_adapter.FiredBoundaryEvent]
    newly_completed: bool


class NotFoundError(Exception):
    pass


class InvalidBpmnError(Exception):
    """Die hochgeladene BPMN-Datei ist nicht parsbar oder die referenzierte/
    automatisch aufgelöste Prozess-ID ist ungültig. Umhüllt
    `spiff_adapter.BpmnParseError`, damit `spiff_adapter` die Fehlerhierarchie
    dieses Moduls nicht kennen muss."""


class ProcessDefinitionInUseError(Exception):
    """Löschung abgelehnt, weil noch Prozessinstanzen existieren."""


class TaskNotReadyError(Exception):
    """Der angegebene Task ist unter den aktuell bereiten Manual/User Tasks
    dieser Instanz nicht (mehr) zu finden - bereits abgeschlossen, falsche
    ID, oder die Instanz ist bereits fertig."""


class InstanceNotRunningError(Exception):
    """`POST /instances/{id}/retry` (P12-S2) auf eine bereits `completed`-Instanz -
    nichts zum Wiederholen."""


async def create_process_definition(
    session: AsyncSession, *, name: str, bpmn_xml: str, process_id: str | None
) -> ProcessDefinition:
    """``name`` ist seit P6-S8 der Prozessfamilien-Schlüssel (2.1a-artiges
    Versionierungsmuster, wie bei Dokumentversionen) - ein Aufruf unter einem
    bereits existierenden Namen legt automatisch die nächste Version an,
    statt abgelehnt zu werden. Frühere Versionen bleiben unverändert
    abrufbar/startbar, kein Überschreiben."""
    max_version = await session.execute(
        select(func.max(ProcessDefinition.version)).where(ProcessDefinition.name == name)
    )
    next_version = (max_version.scalar_one() or 0) + 1

    try:
        _, resolved_process_id = spiff_adapter.parse_bpmn(bpmn_xml, process_id)
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
    """Ohne `name`-Filter wird je Prozessfamilie nur die jeweils neueste
    Version geliefert (`DISTINCT ON`, Postgres-spezifisch wie an anderen
    Stellen dieses Projekts, z. B. `INSERT ... ON CONFLICT DO NOTHING`) -
    eine wachsende Versionshistorie soll die Übersichtsliste nicht zumüllen.
    Mit `name`-Filter wird stattdessen die vollständige Versionshistorie
    dieser einen Familie geliefert, neueste Version zuerst."""
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
    try:
        spec, _ = spiff_adapter.parse_bpmn(definition.bpmn_xml, definition.bpmn_process_id)
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

    # `try`/`finally` statt eines einfachen sequenziellen Ablaufs (P12-S2,
    # Resumability, 7.2): wirft `run_ready_steps()` (z. B. ein `connector_call`-
    # Service-Task, dessen Ziel nicht erreichbar war), MUSS der dadurch entstandene
    # `ERROR`-Zustand trotzdem persistiert werden - sonst gäbe es für
    # `POST /instances/{id}/retry` gar keine Instanz-Zeile mit dem richtigen
    # Zwischenstand zum Fortsetzen. Ohne dieses `finally` würde die Instanz bei
    # einer Exception hier überhaupt nie in der DB landen (real gefunden beim
    # Schreiben des zugehörigen API-Tests).
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

    # completed_by fließt bewusst nicht in die Prozessvariablen ein (würde mit
    # eigenen BPMN-Prozessvariablen kollidieren können) - nur als Event-Payload
    # beim Publizieren in main.py verwendet, siehe dort.
    spiff_adapter.complete_task(task, data)
    # `try`/`finally`: siehe `start_instance` - ein nachfolgender automatischer
    # Schritt (z. B. `connector_call`) kann fehlschlagen, der bereits abgeschlossene
    # Manual Task darf dabei nicht verloren gehen (P12-S2).
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
    """Resumability für einen fehlgeschlagenen automatischen Schritt (7.2, P12-S2) -
    generisches Primitiv, nicht migrationsspezifisch: setzt `ERROR`-Tasks zurück
    (`spiff_adapter.retry_errored_tasks`) und versucht danach erneut, den Workflow
    voranzubringen. Persistiert den neuen Zwischenstand auch bei einem erneuten
    Fehlschlag (`try`/`finally`, siehe `start_instance`) - sonst bliebe die Instanz
    für einen dritten `retry`-Versuch am ursprünglichen Fehlerpunkt hängen, statt am
    tatsächlich letzten (ggf. wieder fehlgeschlagenen) Stand."""
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
    """Ein Poll-Tick der SLA-Zeitüberwachung (P6-S2, ADR 0020): deserialisiert **jede**
    laufende Instanz, lässt fällige Boundary-Timer feuern und persistiert den Blob neu -
    unabhängig davon, ob dabei etwas gefeuert hat, da sich der interne Timer-Zustand
    (nächste Fälligkeit) auch sonst ändern kann. Die bereits in ADR 0019 dokumentierte
    Konsequenz (keine effiziente Cross-Instanz-Abfrage möglich) gilt hier unverändert."""
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
    """Bindeglied zu einem beim Federation Hub laufenden Handover (7.4,
    P6-S9) - siehe `models.FederationTask`. Ein `unique`-Index auf
    `handover_id` verhindert, dass derselbe Handover versehentlich zweimal
    verknüpft wird."""
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
    """Default `direction="outbound"`: `POST /federation/inbound-result` meldet
    stets das Ergebnis eines selbst initiierten (outbound) Handover zurück.
    Ohne den Richtungsfilter wäre die Zeile im Selbst-Loopback-Smoke-Test
    (dieselbe `handover_id` existiert dort sowohl als outbound- als auch als
    inbound-Zeile, siehe `models.FederationTask`) nicht eindeutig."""
    result = await session.execute(
        select(FederationTask).where(
            FederationTask.handover_id == handover_id, FederationTask.direction == direction
        )
    )
    return result.scalar_one_or_none()


async def get_inbound_federation_task_for_instance(
    session: AsyncSession, process_instance_id: str
) -> FederationTask | None:
    """Findet den eingehenden Handover, der diese (per `POST
    /federation/inbound` gestartete) Instanz ausgelöst hat - Grundlage dafür,
    an welche `origin_installation_id`/welchen `handover_id` ein
    `federated_return`-Task in dieser Instanz sein Ergebnis zurückschickt.
    Bewusst vereinfachend genau ein eingehender Handover je Instanz (siehe
    docs/services/workflow-service.md "Offene Punkte")."""
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
    """Verknüpft den bislang task-losen eingehenden `FederationTask`-Eintrag
    (siehe `get_inbound_federation_task_for_instance`) nachträglich mit dem
    `federated_return`-Task, der das Ergebnis tatsächlich zurückgeschickt hat -
    dient danach als Dispatch-Sperre gegen ein doppeltes Zurücksenden
    (`get_federation_task_by_task` findet die Zeile ab jetzt über `task_id`)."""
    federation_task.task_id = task_id
    federation_task.status = status
    federation_task.updated_at = datetime.now(UTC)
    await session.flush()
