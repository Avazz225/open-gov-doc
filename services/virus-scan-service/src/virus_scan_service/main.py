import hashlib
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from virus_scan_service import repository
from virus_scan_service.engines import build_engine as build_scan_engine
from virus_scan_service.models import Base
from virus_scan_service.schemas import ScanResultOut
from virus_scan_service.settings import Settings
from virus_scan_service.storage_client import StorageClient

settings = Settings()
configure_logging(settings)


def _compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS virus_scan"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.storage = StorageClient(settings.storage_service_base_url)
    app.state.scan_engine = build_scan_engine(settings)

    event_bus = NatsEventBusClient(settings.nats_url, stream="virus_scan")
    await event_bus.connect()
    app.state.event_bus = event_bus

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    yield

    if registration:
        await registration.stop()
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


@app.post("/scan", response_model=ScanResultOut, status_code=201)
async def scan_upload(
    file: UploadFile = File(...),
    document_id: str | None = Form(None),
    created_by: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> ScanResultOut:
    """Verpflichtender Scan vor Freigabe eines Uploads (10.3, ADR 0010) - der
    Document Service ruft dies synchron auf, *bevor* er Inhalt/Metadaten
    persistiert. `document_id` ist beim initialen Upload noch unbekannt
    (Dokument existiert erst nach einem sauberen Scan) und daher optional.
    """
    data = await file.read()
    verdict = await app.state.scan_engine.scan(data)

    scan_id = str(uuid.uuid4())
    quarantine_key: str | None = None
    if not verdict.clean:
        # Quarantäne statt automatischem Löschen (10.3): Nachvollziehbarkeit/
        # Beweiswert bleibt erhalten, kein Zugriff über den regulären
        # Dokument-Pfad, da nie ein Dokument dafür angelegt wird.
        quarantine_key = f"quarantine/{scan_id}"
        await app.state.storage.upload(quarantine_key, data, file.content_type)

    result = await repository.create_scan_result(
        session,
        id=scan_id,
        document_id=document_id,
        filename=file.filename or "unbekannt",
        content_type=file.content_type,
        size_bytes=len(data),
        checksum_sha256=_compute_checksum(data),
        status="clean" if verdict.clean else "infected",
        threat_name=verdict.threat_name,
        engine=settings.scan_engine,
        quarantine_object_key=quarantine_key,
        created_by=created_by,
    )
    await session.commit()
    await publish_event(
        "virus_scan.completed",
        subject=scan_id,
        payload={
            "document_id": document_id,
            "filename": result.filename,
            "status": result.status,
            "threat_name": result.threat_name,
            "created_by": created_by,
        },
    )
    return result


@app.get("/scans/{scan_id}", response_model=ScanResultOut)
async def get_scan(scan_id: str, session: AsyncSession = Depends(get_session)) -> ScanResultOut:
    try:
        return await repository.get_scan_result(session, scan_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/scans", response_model=list[ScanResultOut])
async def list_scans(
    document_id: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[ScanResultOut]:
    return await repository.list_scan_results(session, document_id=document_id)
