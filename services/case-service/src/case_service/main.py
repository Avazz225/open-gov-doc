import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_permission_client import PermissionServiceClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from case_service import repository
from case_service.consumer import start_consuming
from case_service.document_client import DocumentClient
from case_service.models import Base
from case_service.object_type_client import ObjectTypeClient
from case_service.schemas import (
    CaseArchivalConfigIn,
    CaseArchivalConfigOut,
    CaseArchiveStatusOut,
    CaseCreate,
    CaseDocumentAdd,
    CaseDocumentReferenceOut,
    CaseDocumentRemove,
    CaseNumberConfigIn,
    CaseNumberConfigOut,
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
        # Aussonderung (5.6, seit P7-S3b) - Ad-hoc-Migration wie ueberall in
        # diesem System (kein Alembic), "case" muss weiterhin gequotet werden.
        await conn.execute(
            text('ALTER TABLE "case".cases ADD COLUMN IF NOT EXISTS archive_after TIMESTAMPTZ')
        )
        await conn.execute(
            text('ALTER TABLE "case".cases ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ')
        )
        # Vorgangsnummer (2.3/2.5, P15-S3) - gleiches Ad-hoc-Migrationsmuster.
        await conn.execute(
            text('ALTER TABLE "case".cases ADD COLUMN IF NOT EXISTS vorgangsnummer VARCHAR(64)')
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.workflow_client = WorkflowClient(settings.workflow_service_base_url)
    app.state.document_client = DocumentClient(settings.document_service_base_url)
    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)

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
    await app.state.permission_client.close()
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


async def _require_case_permission(x_dms_principal: str, *, access_type: str) -> None:
    """RBAC (Post-Roadmap Phase 19 Session 5, ADR 0070) - case-service hatte
    zuvor GAR KEINE Berechtigungsprüfung. Prüft `case.read`/`case.write` an
    der Wurzelressource (`root`), nicht an einer Umlaufmappen-eigenen
    Ressource - case-service registriert (anders als folder-service) keine
    eigenen Knoten im permission-service-Ressourcenbaum, siehe ADR 0070
    "Begründung". Erster Konsument von `libs/dms-permission-client` (P19-S1)
    überhaupt. Die "everyone"-Gruppe (ADR 0067) gewährt `case.read`/
    `case.write` standardmäßig jedem authentifizierten Principal - erhält
    das bisherige De-facto-offene Verhalten, macht es aber admin-editierbar."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    permission = "case.read" if access_type == "read" else "case.write"
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=PermissionServiceClient.ROOT_RESOURCE_ID,
        permission=permission,
        access_type=access_type,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Fehlende Berechtigung {permission!r}")


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
async def create_case(
    payload: CaseCreate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseOut:
    await _require_case_permission(x_dms_principal, access_type="write")
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

    # Vorgangsnummer (2.3/2.5, P15-S3): jede neue Umlaufmappe bekommt ab
    # dieser Session einen server-generierten, installationsweit eindeutigen
    # Bezug (Grundlage für das automatische Zuordnen eingehender Post über
    # den neuen mail-connector).
    vorgangsnummer = await repository.next_vorgangsnummer(session)

    case = await repository.create_case(
        session,
        case_id=case_id,
        name=payload.name,
        object_type_id=payload.object_type_id,
        attributes=payload.attributes,
        process_definition_id=payload.process_definition_id,
        process_instance_id=instance["id"],
        created_by=payload.created_by,
        vorgangsnummer=vorgangsnummer,
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
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[CaseOut]:
    await _require_case_permission(x_dms_principal, access_type="read")
    return await repository.list_cases(session, status=status, object_type_id=object_type_id)


@app.get("/cases/by-vorgangsnummer", response_model=list[CaseOut])
async def list_cases_by_vorgangsnummer(
    value: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[CaseOut]:
    """Für den neuen `mail-connector` (2.5/3.3, P15-S3) - vor
    `/cases/{case_id}` registriert, damit `"by-vorgangsnummer"` nicht als
    `{case_id}` interpretiert wird (gleiche Route-Reihenfolge-Regel wie
    `/cases/due-for-archival` unten)."""
    await _require_case_permission(x_dms_principal, access_type="read")
    return await repository.list_cases_by_vorgangsnummer(session, value)


@app.get("/cases/due-for-archival", response_model=list[CaseOut])
async def list_cases_due_for_archival(
    session: AsyncSession = Depends(get_session),
) -> list[CaseOut]:
    """Interner Aufruf von `archival-service` (5.6, seit P7-S3b) - vor
    `/cases/{case_id}` registriert, damit `"due-for-archival"` nicht als
    `{case_id}` interpretiert wird (gleiche Route-Reihenfolge-Regel wie
    `/documents/deleted` in document-service). Bewusst UNGEGATET (Post-
    Roadmap Phase 19 Session 5, ADR 0070) - reiner Maschine-zu-Maschine-
    Rückruf ohne menschlichen Principal, `archival-service` sendet dafür
    aktuell keinerlei Identitäts-Header. Gleiche, bereits vorbestehende
    Lücke wie `document-service`s analoges `PUT /documents/{id}/archived`
    (ebenfalls ungegatet) - eine allgemeine Service-zu-Service-Authentisierung
    ist eine größere, projektweite Entscheidung außerhalb dieser Session."""
    return await repository.list_due_for_archival(session)


@app.get("/cases/{case_id}", response_model=CaseOut)
async def get_case(
    case_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseOut:
    await _require_case_permission(x_dms_principal, access_type="read")
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
    case_id: str,
    payload: CaseDocumentAdd,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseDocumentReferenceOut:
    await _require_case_permission(x_dms_principal, access_type="write")
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
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseDocumentReferenceOut:
    await _require_case_permission(x_dms_principal, access_type="write")
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
    case_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[CaseDocumentReferenceOut]:
    await _require_case_permission(x_dms_principal, access_type="read")
    try:
        case = await repository.get_case(session, case_id)
        references = await repository.list_document_references(session, case_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return [await _resolve_reference(session, case, reference) for reference in references]


@app.post("/cases/{case_id}/archive-request", response_model=CaseOut)
async def request_case_archive(
    case_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseOut:
    """Manueller Aussonderungs-Trigger (5.6, seit P7-S3b) - `409`, wenn die
    Umlaufmappe noch nicht abgeschlossen ist. Menschliche Aktion (anders als
    `PUT .../archived` unten), daher seit P19-S5 gegated."""
    await _require_case_permission(x_dms_principal, access_type="write")
    try:
        case = await repository.request_archive(session, case_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.CaseNotClosedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return case


@app.get("/cases/{case_id}/archive-status", response_model=CaseArchiveStatusOut)
async def get_case_archive_status(
    case_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseArchiveStatusOut:
    await _require_case_permission(x_dms_principal, access_type="read")
    try:
        case = await repository.get_case(session, case_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CaseArchiveStatusOut(
        case_id=case.id, archive_after=case.archive_after, archived_at=case.archived_at
    )


@app.put("/cases/{case_id}/archived", response_model=CaseOut)
async def mark_case_archived(case_id: str, session: AsyncSession = Depends(get_session)) -> CaseOut:
    """Interner Rueckruf von `archival-service`, sobald das XDOMEA-Paket
    verifiziert ist (5.6, seit P7-S3b). Bewusst UNGEGATET (Post-Roadmap
    Phase 19 Session 5, ADR 0070) - gleiche Begründung wie
    `GET /cases/due-for-archival` oben: reiner Maschine-zu-Maschine-Rückruf,
    `archival-service` sendet dafür keinen `X-DMS-Principal`."""
    try:
        case = await repository.mark_archived(session, case_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "case.archived", subject=case_id, payload={}, actor="system:archival-service"
    )
    return case


@app.get("/case-archival-config", response_model=CaseArchivalConfigOut)
async def get_case_archival_config(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseArchivalConfigOut:
    await _require_case_permission(x_dms_principal, access_type="read")
    return await repository.get_archival_config(session)


@app.put("/case-archival-config", response_model=CaseArchivalConfigOut)
async def update_case_archival_config(
    payload: CaseArchivalConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseArchivalConfigOut:
    await _require_case_permission(x_dms_principal, access_type="write")
    config = await repository.update_archival_config(
        session,
        default_archive_after_days_closed=payload.default_archive_after_days_closed,
        archive_encryption_enabled=payload.archive_encryption_enabled,
    )
    await session.commit()
    return config


@app.get("/case-number-config", response_model=CaseNumberConfigOut)
async def get_case_number_config(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseNumberConfigOut:
    await _require_case_permission(x_dms_principal, access_type="read")
    return await repository.get_case_number_config(session)


@app.put("/case-number-config", response_model=CaseNumberConfigOut)
async def update_case_number_config(
    payload: CaseNumberConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseNumberConfigOut:
    await _require_case_permission(x_dms_principal, access_type="write")
    try:
        config = await repository.update_case_number_format(session, format=payload.format)
    except repository.InvalidFieldError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await session.commit()
    return config
