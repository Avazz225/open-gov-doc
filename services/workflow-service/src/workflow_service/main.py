import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workflow_service import repository
from workflow_service.models import Base
from workflow_service.permission_client import PermissionServiceClient
from workflow_service.schemas import (
    ProcessDefinitionDetailOut,
    ProcessDefinitionOut,
    ProcessInstanceCreate,
    ProcessInstanceOut,
    ReadyTaskOut,
    TaskCompleteRequest,
)
from workflow_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def _sla_poll_loop(
    session_factory: async_sessionmaker[AsyncSession], permission_client: PermissionServiceClient
) -> None:
    """SLA-Zeitüberwachung (P6-S2, ADR 0020): pollt statt push-basiert zu reagieren,
    da weder SpiffWorkflow noch dieses Projekt einen Hintergrund-Scheduler mitbringen.
    Ein Fehler in einem Tick bricht die Schleife nicht ab, damit ein einzelner defekter
    Blob nicht die SLA-Überwachung aller anderen laufenden Instanzen stoppt.
    Seit P6-S6 zusätzlich: überspringt den Tick während aktivem Wartungsmodus (4.8) -
    "geplante/periodische Jobs werden angehalten"."""
    while True:
        try:
            if await permission_client.is_maintenance_active():
                await asyncio.sleep(settings.sla_poll_interval_seconds)
                continue
            async with session_factory() as session:
                results = await repository.advance_timers(session)
                await session.commit()
            for result in results:
                for fired in result.fired:
                    await publish_event(
                        "workflow.task.escalated",
                        subject=result.instance.id,
                        payload={
                            "process_definition_id": result.instance.process_definition_id,
                            "business_key": result.instance.business_key,
                            "task_name": fired.name,
                            "lane": fired.lane,
                            "escalation_email": fired.data.get("escalation_email"),
                        },
                    )
                if result.newly_completed:
                    await publish_event(
                        "workflow.instance.completed",
                        subject=result.instance.id,
                        payload={"business_key": result.instance.business_key},
                    )
        except Exception:
            logger.exception(
                "SLA-Poll-Tick fehlgeschlagen - wird beim nächsten Tick erneut versucht."
            )
        await asyncio.sleep(settings.sla_poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS workflow"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)

    # Reiner Producer (kein Consumer, siehe docs/services/workflow-service.md
    # "Events") - eigener Stream, ein Producer muss ihn selbst anlegen (ADR 0001).
    event_bus = NatsEventBusClient(settings.nats_url, stream="workflow")
    await event_bus.connect()
    app.state.event_bus = event_bus

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    sla_poll_task = asyncio.create_task(
        _sla_poll_loop(app.state.session_factory, app.state.permission_client)
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    sla_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await sla_poll_task
    if registration:
        await registration.stop()
    await event_bus.close()
    await app.state.permission_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(event_type: str, subject: str, payload: dict) -> None:
    event = Event(
        event_type=event_type, service_name=settings.service_name, subject=subject, payload=payload
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


async def _require_object_config(x_dms_principal: str) -> None:
    """Retrofit P6-S6: Prozessdefinitionen (inkl. Script-Task-Upload, laut
    `docs/services/workflow-service.md` "ein reales Sicherheitsthema") sind
    ab jetzt eine administrative Aktion, keine reguläre Fachnutzung -
    verlangt die Domain-Admin-Capability `admin.object_config` (dieselbe
    Rolle "Objekttyp-/Workflow-Konfiguration" aus P6-S5, jetzt zum ersten Mal
    tatsächlich durchgesetzt inkl. echtem technischen Konto `config-admin`).
    Instanzstart/Task-Abschluss bleiben bewusst für jeden authentifizierten
    Principal offen (normale Fachnutzung), siehe P6-S6-Rückfrage-Entscheidung."""
    allowed = bool(x_dms_principal) and await app.state.permission_client.has_permission(
        x_dms_principal, "admin.object_config"
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Objekttyp-/Workflow-Konfiguration'",
        )


async def _reject_during_maintenance(x_dms_maintenance_active: str) -> None:
    """Not-Shutdown (4.8, P6-S6): "alle laufenden Workflow-Instanzen ...
    angehalten" wird als "keine neuen Instanzen/keine Fortschritte während
    der Sperre" umgesetzt (siehe ADR 0024 für die Begründung der Grenze)."""
    if x_dms_maintenance_active.lower() == "true":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Systemweite Notfallsperre aktiv - Wartungsmodus",
        )


@app.post(
    "/process-definitions", response_model=ProcessDefinitionOut, status_code=status.HTTP_201_CREATED
)
async def create_process_definition(
    bpmn_xml: UploadFile = File(...),
    name: str = Form(...),
    process_id: str | None = Form(None),
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ProcessDefinitionOut:
    await _require_object_config(x_dms_principal)
    xml_bytes = await bpmn_xml.read()
    try:
        xml_text = xml_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="bpmn_xml ist kein gültiges UTF-8") from exc

    try:
        definition = await repository.create_process_definition(
            session, name=name, bpmn_xml=xml_text, process_id=process_id
        )
    except repository.DuplicateNameError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except repository.InvalidBpmnError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    return definition


@app.get("/process-definitions", response_model=list[ProcessDefinitionOut])
async def list_process_definitions(
    session: AsyncSession = Depends(get_session),
) -> list[ProcessDefinitionOut]:
    return await repository.list_process_definitions(session)


@app.get("/process-definitions/{process_definition_id}", response_model=ProcessDefinitionDetailOut)
async def get_process_definition(
    process_definition_id: int, session: AsyncSession = Depends(get_session)
) -> ProcessDefinitionDetailOut:
    try:
        return await repository.get_process_definition(session, process_definition_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/process-definitions/{process_definition_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_process_definition(
    process_definition_id: int,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_object_config(x_dms_principal)
    try:
        await repository.delete_process_definition(session, process_definition_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.ProcessDefinitionInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()


@app.post(
    "/process-definitions/{process_definition_id}/instances",
    response_model=ProcessInstanceOut,
    status_code=status.HTTP_201_CREATED,
)
async def start_instance(
    process_definition_id: int,
    payload: ProcessInstanceCreate,
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> ProcessInstanceOut:
    await _reject_during_maintenance(x_dms_maintenance_active)
    try:
        instance = await repository.start_instance(
            session,
            process_definition_id,
            created_by=payload.created_by,
            business_key=payload.business_key,
            initial_data=payload.initial_data,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.InvalidBpmnError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "workflow.instance.started",
        subject=instance.id,
        payload={"process_definition_id": process_definition_id, "created_by": payload.created_by},
    )
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance.id,
            payload={"business_key": instance.business_key},
        )
    return instance


@app.get("/instances/{instance_id}", response_model=ProcessInstanceOut)
async def get_instance(
    instance_id: str, session: AsyncSession = Depends(get_session)
) -> ProcessInstanceOut:
    try:
        return await repository.get_instance(session, instance_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/instances", response_model=list[ProcessInstanceOut])
async def list_instances(
    process_definition_id: int | None = None,
    status: str | None = None,
    business_key: str | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[ProcessInstanceOut]:
    return await repository.list_instances(
        session,
        process_definition_id=process_definition_id,
        status=status,
        business_key=business_key,
    )


@app.get("/instances/{instance_id}/tasks", response_model=list[ReadyTaskOut])
async def get_ready_tasks(
    instance_id: str, session: AsyncSession = Depends(get_session)
) -> list[ReadyTaskOut]:
    try:
        tasks = await repository.get_ready_tasks(session, instance_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [ReadyTaskOut(id=t.id, name=t.name, lane=t.lane, data=t.data) for t in tasks]


@app.post("/instances/{instance_id}/tasks/{task_id}/complete", response_model=ProcessInstanceOut)
async def complete_task(
    instance_id: str,
    task_id: str,
    payload: TaskCompleteRequest,
    x_dms_maintenance_active: str = Header(default="false"),
    session: AsyncSession = Depends(get_session),
) -> ProcessInstanceOut:
    await _reject_during_maintenance(x_dms_maintenance_active)
    try:
        instance = await repository.complete_task(
            session, instance_id, task_id, completed_by=payload.completed_by, data=payload.data
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.TaskNotReadyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "workflow.task.completed",
        subject=instance_id,
        payload={"task_id": task_id, "completed_by": payload.completed_by},
    )
    if instance.status == "completed":
        await publish_event(
            "workflow.instance.completed",
            subject=instance_id,
            payload={"business_key": instance.business_key},
        )
    return instance
