import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from document_service import repository
from document_service.content_type_sniffer import sniff_content_type
from document_service.folder_client import FolderClient
from document_service.models import Base
from document_service.object_type_client import ObjectTypeClient
from document_service.schemas import (
    CheckinResult,
    DocumentOut,
    DocumentUpdate,
    DocumentVersionOut,
    LockAcquireRequest,
    LockForceReleaseRequest,
    LockOut,
    LockReleaseRequest,
    UploadConfigIn,
    UploadConfigOut,
)
from document_service.settings import Settings
from document_service.storage_client import ObjectNotFoundError, StorageClient, compute_checksum
from document_service.virus_scan_client import (
    ScanRejectedError,
    ScanUnavailableError,
    VirusScanClient,
)

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


# Reservierter Attributschlüssel für den Kennzeichengenerator (2.2, P5e-S2) -
# ein vom Client mitgesendeter Wert wird bei der Anlage verworfen, die
# Vergabe erfolgt ausschließlich serverseitig über den Object-Type Service.
KENNZEICHEN_ATTRIBUTE = "Kennzeichen"


def _has_kennzeichen_admin_role(x_dms_roles: str) -> bool:
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return settings.kennzeichen_admin_role in roles


def _object_key(document_id: str, checksum_sha256: str) -> str:
    # Inhaltsadressiert statt versionsnummer-basiert (2.1a): vermeidet die
    # Henne-Ei-Reihenfolge "Upload braucht Versionsnummer, Versionsnummer
    # braucht abgeschlossenen DB-Schreibzugriff" und dedupliziert identische
    # Inhalte innerhalb desselben Dokuments automatisch.
    return f"documents/{document_id}/{checksum_sha256}"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS document"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc-Schema-Erweiterung (kein Alembic in dieser frühen Phase, siehe
        # CONTRIBUTING.md): `create_all` legt fehlende TABELLEN an, ändert aber
        # keine bestehenden - `attributes` kam erst in P3-S3 dazu. Idempotent
        # dank IF NOT EXISTS, betrifft nur additive, defaultbehaftete Spalten.
        await conn.execute(
            text(
                "ALTER TABLE document.document "
                "ADD COLUMN IF NOT EXISTS attributes JSON DEFAULT '{}'::json NOT NULL"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.storage = StorageClient(settings.storage_service_base_url)
    app.state.folder_client = FolderClient(settings.folder_service_base_url)
    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)
    app.state.virus_scan_client = VirusScanClient(settings.virus_scan_service_base_url)

    event_bus = NatsEventBusClient(settings.nats_url, stream="document")
    await event_bus.connect()
    app.state.event_bus = event_bus

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
    await event_bus.close()
    await app.state.storage.close()
    await app.state.folder_client.close()
    await app.state.object_type_client.close()
    await app.state.virus_scan_client.close()
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


async def _resolve_content_type(session: AsyncSession, data: bytes) -> str:
    """Sniffing statt ungeprüftem Client-Header (P5d-S1) + Abgleich gegen die
    admin-editierbare Format-Whitelist. Wird vor dem Virenscan aufgerufen -
    ein von vornherein abgelehntes Format muss den Scan-Dienst nicht erst
    bemühen."""
    content_type = sniff_content_type(data)
    config = await repository.get_upload_config(session)
    if config.allowed_content_types and content_type not in config.allowed_content_types:
        raise HTTPException(
            status_code=400,
            detail=f"Content-Type {content_type!r} ist nicht in der erlaubten Formatliste",
        )
    return content_type


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/upload-config", response_model=UploadConfigOut)
async def get_upload_config(session: AsyncSession = Depends(get_session)) -> UploadConfigOut:
    config = await repository.get_upload_config(session)
    await session.commit()
    return config


@app.put("/upload-config", response_model=UploadConfigOut)
async def put_upload_config(
    body: UploadConfigIn, session: AsyncSession = Depends(get_session)
) -> UploadConfigOut:
    config = await repository.update_upload_config(
        session, allowed_content_types=body.allowed_content_types
    )
    await session.commit()
    return config


@app.post("/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_document(
    file: UploadFile = File(...),
    title: str = Form(...),
    created_by: str = Form(...),
    folder_id: str | None = Form(None),
    object_type_id: int | None = Form(None),
    attributes: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    try:
        parsed_attributes = json.loads(attributes) if attributes else {}
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="attributes ist kein gültiges JSON") from exc
    parsed_attributes.pop(KENNZEICHEN_ATTRIBUTE, None)

    parent_folder = None
    if folder_id is not None:
        parent_folder = await app.state.folder_client.get(folder_id)
        if parent_folder is None:
            raise HTTPException(status_code=400, detail=f"folder_id {folder_id!r} unbekannt")

    if object_type_id is not None:
        errors = await app.state.object_type_client.validate(
            object_type_id,
            name=title,
            attributes=parsed_attributes,
            parent_object_type_id=parent_folder["object_type_id"] if parent_folder else None,
            parent_is_root=folder_id is None or folder_id == "root",
        )
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})

        kennzeichen = await app.state.object_type_client.next_kennzeichen(object_type_id)
        if kennzeichen is not None:
            parsed_attributes[KENNZEICHEN_ATTRIBUTE] = kennzeichen

    data = await file.read()
    content_type = await _resolve_content_type(session, data)

    try:
        await app.state.virus_scan_client.scan(
            data=data,
            filename=file.filename or title,
            content_type=content_type,
            document_id=None,
            created_by=created_by,
        )
    except ScanRejectedError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "virus_detected", "threat_name": exc.threat_name}
        ) from exc
    except ScanUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Virenscan-Dienst nicht erreichbar - Upload abgelehnt"
        ) from exc

    checksum = compute_checksum(data)
    document_id = str(uuid.uuid4())
    key = _object_key(document_id, checksum)
    await app.state.storage.upload(key, data, content_type)

    document = await repository.create_document(
        session,
        document_id=document_id,
        title=title,
        filename=file.filename or title,
        content_type=content_type,
        size_bytes=len(data),
        checksum_sha256=checksum,
        storage_object_key=key,
        folder_id=folder_id,
        object_type_id=object_type_id,
        attributes=parsed_attributes,
        created_by=created_by,
    )
    await session.commit()
    await publish_event(
        "document.created", subject=document_id, payload={"title": title, "created_by": created_by}
    )
    return document


@app.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    folder_id: str, session: AsyncSession = Depends(get_session)
) -> list[DocumentOut]:
    return await repository.list_documents_by_folder(session, folder_id)


@app.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    try:
        return await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.patch("/documents/{document_id}", response_model=DocumentOut)
async def update_document(
    document_id: str,
    payload: DocumentUpdate,
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    try:
        document = await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if payload.attributes is not None:
        old_kennzeichen = document.attributes.get(KENNZEICHEN_ATTRIBUTE)
        new_kennzeichen = payload.attributes.get(KENNZEICHEN_ATTRIBUTE)
        if new_kennzeichen != old_kennzeichen and not _has_kennzeichen_admin_role(x_dms_roles):
            raise HTTPException(
                status_code=403,
                detail=f"Nur die Rolle {settings.kennzeichen_admin_role!r} darf das "
                f"Attribut {KENNZEICHEN_ATTRIBUTE!r} ändern",
            )

    if document.object_type_id is not None:
        errors = await app.state.object_type_client.validate(
            document.object_type_id,
            name=payload.title if payload.title is not None else document.title,
            attributes=payload.attributes
            if payload.attributes is not None
            else document.attributes,
        )
        if errors:
            raise HTTPException(status_code=400, detail={"errors": errors})

    updated = await repository.update_document_metadata(
        session, document_id, title=payload.title, attributes=payload.attributes
    )
    await session.commit()
    await publish_event(
        "document.metadata.updated", subject=document_id, payload={"title": updated.title}
    )
    return updated


@app.delete("/documents/{document_id}", response_model=DocumentOut)
async def delete_document(
    document_id: str, deleted_by: str, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    try:
        document = await repository.delete_document(session, document_id, deleted_by=deleted_by)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event("document.deleted", subject=document_id, payload={"deleted_by": deleted_by})
    return document


@app.get("/documents/{document_id}/versions", response_model=list[DocumentVersionOut])
async def list_versions(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> list[DocumentVersionOut]:
    try:
        return await repository.list_versions(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/documents/{document_id}/versions/{version_number}", response_model=DocumentVersionOut)
async def get_version(
    document_id: str, version_number: int, session: AsyncSession = Depends(get_session)
) -> DocumentVersionOut:
    try:
        return await repository.get_version(session, document_id, version_number)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/documents/{document_id}/content")
async def download_current_content(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> Response:
    try:
        version = await repository.get_current_version(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        data = await app.state.storage.download(version.storage_object_key)
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Inhalt im Storage Service nicht (mehr) vorhanden"
        ) from exc
    return Response(content=data, media_type=version.content_type or "application/octet-stream")


@app.get("/documents/{document_id}/versions/{version_number}/content")
async def download_version_content(
    document_id: str, version_number: int, session: AsyncSession = Depends(get_session)
) -> Response:
    try:
        version = await repository.get_version(session, document_id, version_number)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        data = await app.state.storage.download(version.storage_object_key)
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Inhalt im Storage Service nicht (mehr) vorhanden"
        ) from exc
    return Response(content=data, media_type=version.content_type or "application/octet-stream")


@app.post("/documents/{document_id}/versions", response_model=CheckinResult, status_code=201)
async def checkin_version(
    document_id: str,
    file: UploadFile = File(...),
    expected_base_version_number: int = Form(...),
    created_by: str = Form(...),
    comment: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> CheckinResult:
    data = await file.read()
    content_type = await _resolve_content_type(session, data)

    try:
        await app.state.virus_scan_client.scan(
            data=data,
            filename=file.filename or f"version-{expected_base_version_number + 1}",
            content_type=content_type,
            document_id=document_id,
            created_by=created_by,
        )
    except ScanRejectedError as exc:
        raise HTTPException(
            status_code=422, detail={"error": "virus_detected", "threat_name": exc.threat_name}
        ) from exc
    except ScanUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="Virenscan-Dienst nicht erreichbar - Upload abgelehnt"
        ) from exc

    checksum = compute_checksum(data)
    key = _object_key(document_id, checksum)
    await app.state.storage.upload(key, data, content_type)

    try:
        version, is_conflict = await repository.checkin_version(
            session,
            document_id,
            expected_base_version_number=expected_base_version_number,
            storage_object_key=key,
            filename=file.filename or f"version-{expected_base_version_number + 1}",
            content_type=content_type,
            size_bytes=len(data),
            checksum_sha256=checksum,
            created_by=created_by,
            comment=comment,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.LockConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    await session.commit()
    await publish_event(
        "document.version.created",
        subject=document_id,
        payload={
            "version_number": version.version_number,
            "is_conflict": is_conflict,
            "created_by": created_by,
        },
    )
    return CheckinResult(version=version, is_conflict=is_conflict)


@app.get("/documents/{document_id}/lock", response_model=LockOut | None)
async def get_lock(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> LockOut | None:
    return await repository.get_lock(session, document_id)


@app.post("/documents/{document_id}/lock", response_model=LockOut, status_code=201)
async def acquire_lock(
    document_id: str, payload: LockAcquireRequest, session: AsyncSession = Depends(get_session)
) -> LockOut:
    try:
        lock = await repository.acquire_lock(
            session,
            document_id,
            locked_by=payload.locked_by,
            session_id=payload.session_id,
            timeout_seconds=payload.timeout_seconds or settings.default_lock_timeout_seconds,
        )
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repository.LockConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    return lock


@app.delete("/documents/{document_id}/lock", status_code=204)
async def release_lock(
    document_id: str, payload: LockReleaseRequest, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.release_lock(session, document_id, released_by=payload.released_by)
    except repository.LockNotHeldError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    await session.commit()


@app.post("/documents/{document_id}/lock/force-release", response_model=LockOut)
async def force_release_lock(
    document_id: str, payload: LockForceReleaseRequest, session: AsyncSession = Depends(get_session)
) -> LockOut:
    """Administrativer Force-Unlock (4.2) - besonders sensibler Audit-Fall.
    Optionales Vier-Augen-Prinzip (4.3) ist noch nicht verdrahtet (folgt mit
    dem generischen Approval-Mechanismus in P6-S4); dieser Endpunkt ist bis
    dahin ungated und muss auf API-Gateway-Ebene (P4-S1) entsprechend restriktiv
    autorisiert werden."""
    try:
        original_lock = await repository.force_release_lock(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "document.lock.force_released",
        subject=document_id,
        payload={
            "original_locked_by": original_lock.locked_by,
            "released_by": payload.released_by,
            "reason": payload.reason,
        },
    )
    return original_lock
