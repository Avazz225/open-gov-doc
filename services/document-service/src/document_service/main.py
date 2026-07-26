import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from fastapi import Depends, FastAPI, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from document_service import repository
from document_service.models import Base
from document_service.schemas import (
    CheckinResult,
    DocumentOut,
    DocumentVersionOut,
    LockAcquireRequest,
    LockForceReleaseRequest,
    LockOut,
    LockReleaseRequest,
)
from document_service.settings import Settings
from document_service.storage_client import StorageClient, compute_checksum

settings = Settings()
configure_logging(settings)


def _object_key(document_id: str, checksum_sha256: str) -> str:
    # Inhaltsadressiert statt versionsnummer-basiert (2.1a): vermeidet die
    # Henne-Ei-Reihenfolge "Upload braucht Versionsnummer, Versionsnummer
    # braucht abgeschlossenen DB-Schreibzugriff" und dedupliziert identische
    # Inhalte innerhalb desselben Dokuments automatisch.
    return f"documents/{document_id}/{checksum_sha256}"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS document"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.storage = StorageClient(settings.storage_service_base_url)

    event_bus = NatsEventBusClient(settings.nats_url, stream="document")
    await event_bus.connect()
    app.state.event_bus = event_bus

    yield

    await event_bus.close()
    await app.state.storage.close()
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


@app.post("/documents", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def create_document(
    file: UploadFile = File(...),
    title: str = Form(...),
    created_by: str = Form(...),
    folder_id: str | None = Form(None),
    object_type_id: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> DocumentOut:
    data = await file.read()
    checksum = compute_checksum(data)
    document_id = str(uuid.uuid4())
    key = _object_key(document_id, checksum)
    await app.state.storage.upload(key, data, file.content_type)

    document = await repository.create_document(
        session,
        document_id=document_id,
        title=title,
        filename=file.filename or title,
        content_type=file.content_type,
        size_bytes=len(data),
        checksum_sha256=checksum,
        storage_object_key=key,
        folder_id=folder_id,
        object_type_id=object_type_id,
        created_by=created_by,
    )
    await session.commit()
    await publish_event(
        "document.created", subject=document_id, payload={"title": title, "created_by": created_by}
    )
    return document


@app.get("/documents/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: str, session: AsyncSession = Depends(get_session)
) -> DocumentOut:
    try:
        return await repository.get_document(session, document_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    data = await app.state.storage.download(version.storage_object_key)
    return Response(content=data, media_type=version.content_type or "application/octet-stream")


@app.get("/documents/{document_id}/versions/{version_number}/content")
async def download_version_content(
    document_id: str, version_number: int, session: AsyncSession = Depends(get_session)
) -> Response:
    try:
        version = await repository.get_version(session, document_id, version_number)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    data = await app.state.storage.download(version.storage_object_key)
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
    checksum = compute_checksum(data)
    key = _object_key(document_id, checksum)
    await app.state.storage.upload(key, data, file.content_type)

    try:
        version, is_conflict = await repository.checkin_version(
            session,
            document_id,
            expected_base_version_number=expected_base_version_number,
            storage_object_key=key,
            filename=file.filename or f"version-{expected_base_version_number + 1}",
            content_type=file.content_type,
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
