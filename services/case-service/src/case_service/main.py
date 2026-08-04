import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from case_service import repository
from case_service.consumer import start_consuming
from case_service.document_client import DocumentClient
from case_service.models import Base
from case_service.object_type_client import ObjectTypeClient
from case_service.schemas import (
    CaseCreate,
    CaseDocumentAdd,
    CaseDocumentReferenceOut,
    CaseDocumentRemove,
    CaseOut,
)
from case_service.settings import Settings
from case_service.workflow_client import ProcessDefinitionUnknownError, WorkflowClient

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        # "case" ist ein reserviertes SQL-Schluesselwort (CASE WHEN) - anders
        # als Base.metadata.create_all (quotet automatisch ueber SQLAlchemys
        # IdentifierPreparer) muss dieser rohe SQL-String selbst quoten.
        await conn.execute(text('CREATE SCHEMA IF NOT EXISTS "case"'))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.workflow_client = WorkflowClient(settings.workflow_service_base_url)
    app.state.document_client = DocumentClient(settings.document_service_base_url)
    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)

    # Producer (eigener Stream "case", `case.created`/`.document.added`/
    # `.document.removed`/`.closed`) UND Konsument (`workflow.instance.completed`)
    # - zwei getrennte Client-Instanzen, gleiche Konvention wie notification-service
    # (siehe dessen main.py-Kommentar zur Begruendung).
    producer = NatsEventBusClient(settings.nats_url, stream="case")
    await producer.connect()
    app.state.producer = producer

    consumer = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer.connect()
    app.state.consumer = consumer
    await start_consuming(
        consumer,
        settings.subjects,
        app.state.session_factory,
        app.state.document_client,
        publish_event,
    )

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    if registration:
        await registration.stop()
    await consumer.close()
    await producer.close()
    await app.state.workflow_client.close()
    await app.state.document_client.close()
    await app.state.object_type_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(
    event_type: str, subject: str, payload: dict, actor: str | None = None
) -> None:
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
        actor=actor,
    )
    await app.state.producer.publish(event_type, event.to_bytes())


async def _resolve_reference(session: AsyncSession, case, reference) -> CaseDocumentReferenceOut:
    """Zweistufiges Referenzmodell (2.3): waehrend die Umlaufmappe offen ist,
    wird die aktuelle Hauptversion live aus dem Document Service gelesen -
    ab Abschluss zaehlt ausschliesslich der fixierte Abschluss-Snapshot,
    ohne weiteren document-service-Aufruf."""
    current_version_number = None
    document_deleted_at = None
    if case.status == "open" and reference.removed_at is None:
        document = await app.state.document_client.get(reference.document_id)
        if document is not None:
            current_version_number = document["current_version_number"]
            document_deleted_at = document["deleted_at"]
    return CaseDocumentReferenceOut(
        document_id=reference.document_id,
        added_by=reference.added_by,
        added_at=reference.added_at,
        removed_by=reference.removed_by,
        removed_at=reference.removed_at,
        snapshot_version_number=reference.snapshot_version_number,
        current_version_number=current_version_number,
        document_deleted_at=document_deleted_at,
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/cases", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
async def create_case(payload: CaseCreate, session: AsyncSession = Depends(get_session)) -> CaseOut:
    if payload.object_type_id is not None:
        errors = await app.state.object_type_client.validate(
            payload.object_type_id, name=payload.name, attributes=payload.attributes
        )
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})

    case_id = str(uuid.uuid4())
    try:
        instance = await app.state.workflow_client.start_instance(
            payload.process_definition_id,
            created_by=payload.created_by,
            business_key=case_id,
            initial_data=payload.initial_data,
        )
    except ProcessDefinitionUnknownError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    case = await repository.create_case(
        session,
        case_id=case_id,
        name=payload.name,
        object_type_id=payload.object_type_id,
        attributes=payload.attributes,
        process_definition_id=payload.process_definition_id,
        process_instance_id=instance["id"],
        created_by=payload.created_by,
    )
    await session.commit()
    await publish_event(
        "case.created",
        subject=case_id,
        payload={"name": payload.name, "created_by": payload.created_by},
        actor=payload.created_by,
    )
    return case


@app.get("/cases", response_model=list[CaseOut])
async def list_cases(
    status: str | None = None,
    object_type_id: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[CaseOut]:
    return await repository.list_cases(session, status=status, object_type_id=object_type_id)


@app.get("/cases/{case_id}", response_model=CaseOut)
async def get_case(case_id: str, session: AsyncSession = Depends(get_session)) -> CaseOut:
    try:
        return await repository.get_case(session, case_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post(
    "/cases/{case_id}/documents",
    response_model=CaseDocumentReferenceOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_case_document(
    case_id: str, payload: CaseDocumentAdd, session: AsyncSession = Depends(get_session)
) -> CaseDocumentReferenceOut:
    document = await app.state.document_client.get(payload.document_id)
    if document is None:
        raise HTTPException(
            status_code=400, detail=f"document_id {payload.document_id!r} unbekannt"
        )
    try:
        reference = await repository.add_document_reference(
            session, case_id, document_id=payload.document_id, added_by=payload.added_by
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.CaseClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    case = await repository.get_case(session, case_id)
    await session.commit()
    await publish_event(
        "case.document.added",
        subject=case_id,
        payload={"document_id": payload.document_id, "added_by": payload.added_by},
        actor=payload.added_by,
    )
    return await _resolve_reference(session, case, reference)


@app.delete("/cases/{case_id}/documents/{document_id}", response_model=CaseDocumentReferenceOut)
async def remove_case_document(
    case_id: str,
    document_id: str,
    payload: CaseDocumentRemove,
    session: AsyncSession = Depends(get_session),
) -> CaseDocumentReferenceOut:
    try:
        reference = await repository.remove_document_reference(
            session, case_id, document_id, removed_by=payload.removed_by
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.CaseClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    case = await repository.get_case(session, case_id)
    await session.commit()
    await publish_event(
        "case.document.removed",
        subject=case_id,
        payload={"document_id": document_id, "removed_by": payload.removed_by},
        actor=payload.removed_by,
    )
    return await _resolve_reference(session, case, reference)


@app.get("/cases/{case_id}/documents", response_model=list[CaseDocumentReferenceOut])
async def list_case_documents(
    case_id: str, session: AsyncSession = Depends(get_session)
) -> list[CaseDocumentReferenceOut]:
    try:
        case = await repository.get_case(session, case_id)
        references = await repository.list_document_references(session, case_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [await _resolve_reference(session, case, reference) for reference in references]
