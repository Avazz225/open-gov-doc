import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_permission_client import PermissionServiceClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from rendering_service import repository
from rendering_service.consumer import start_consuming, start_consuming_ocr
from rendering_service.document_client import DocumentServiceClient
from rendering_service.models import Base
from rendering_service.ocr_client import OcrServiceClient
from rendering_service.schemas import RenditionOut
from rendering_service.settings import Settings
from rendering_service.storage_client import StorageClient
from rendering_service.watermark import add_text_watermark

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS rendering"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.document_client = DocumentServiceClient(settings.document_service_base_url)
    app.state.storage = StorageClient(settings.storage_service_base_url)
    app.state.ocr_client = OcrServiceClient(settings.ocr_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)

    # Reiner Konsument fremder Streams (`document.>`, ensure_stream=False) und
    # eigener Producer-Client (eigener Stream "rendering") getrennt, wie bei
    # permission-service - ein Producer muss seinen Stream selbst anlegen,
    # siehe ADR 0001.
    event_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await event_bus.connect()
    app.state.event_bus = event_bus

    publisher = NatsEventBusClient(settings.nats_url, stream="rendering", ensure_stream=True)
    await publisher.connect()
    app.state.publisher = publisher

    await start_consuming(
        event_bus,
        settings.document_subjects,
        session_factory=app.state.session_factory,
        document_client=app.state.document_client,
        storage=app.state.storage,
        publish_event=publish_event,
    )
    # Nachzieheffekt aus P5-S3 (2.4/3.9): zweites Abo auf demselben event_bus-
    # Client, aber auf dem "ocr"-Stream statt "document" - siehe consumer.py.
    await start_consuming_ocr(
        event_bus,
        settings.ocr_subjects,
        session_factory=app.state.session_factory,
        ocr_client=app.state.ocr_client,
        storage=app.state.storage,
        publish_event=publish_event,
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
    await publisher.close()
    await event_bus.close()
    await app.state.document_client.close()
    await app.state.storage.close()
    await app.state.ocr_client.close()
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
    await app.state.publisher.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


async def _require_rendering_permission(x_dms_principal: str, *, access_type: str) -> None:
    """RBAC (Post-Roadmap Phase 19 Session 8, ADR 0073) - rendering-service
    hatte zuvor GAR KEINE Berechtigungsprüfung. Prüft `rendering.read`/
    `rendering.write` an der Wurzelressource (`root`) - rendering-service
    registriert keine eigenen Ressourcen-Baumknoten. Die "everyone"-Gruppe
    (ADR 0067) gewährt beide standardmäßig - erhält das bisherige De-facto-
    offene Verhalten, macht es aber admin-editierbar."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    permission = "rendering.read" if access_type == "read" else "rendering.write"
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=PermissionServiceClient.ROOT_RESOURCE_ID,
        permission=permission,
        access_type=access_type,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Fehlende Berechtigung {permission!r}")


@app.get("/renditions", response_model=list[RenditionOut])
async def list_renditions(
    document_id: str,
    version_number: int | None = None,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[RenditionOut]:
    await _require_rendering_permission(x_dms_principal, access_type="read")
    return await repository.list_renditions(
        session, document_id=document_id, version_number=version_number
    )


@app.get("/renditions/{rendition_id}", response_model=RenditionOut)
async def get_rendition(
    rendition_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> RenditionOut:
    await _require_rendering_permission(x_dms_principal, access_type="read")
    try:
        return await repository.get_rendition(session, rendition_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/renditions/{rendition_id}/content")
async def download_rendition_content(
    rendition_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _require_rendering_permission(x_dms_principal, access_type="read")
    try:
        rendition = await repository.get_rendition(session, rendition_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if rendition.status != "ready":
        raise HTTPException(
            status_code=409, detail=f"Ersatzdarstellung hat Status {rendition.status!r}"
        )
    data = await app.state.storage.download(rendition.storage_object_key)
    return Response(content=data, media_type=rendition.target_content_type)


@app.post("/render/watermark")
async def render_watermark(
    file: UploadFile = File(...),
    text_: str = Form(..., alias="text"),
    x_dms_principal: str = Header(default=""),
) -> Response:
    """On-Demand-Wasserzeichen (3.7) - kein automatischer Pipelineschritt,
    keine Persistenz (siehe watermark.py)."""
    await _require_rendering_permission(x_dms_principal, access_type="write")
    data = await file.read()
    try:
        watermarked = add_text_watermark(data, text_)
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"Wasserzeichen konnte nicht angewendet werden: {exc}"
        ) from exc
    return Response(content=watermarked, media_type="application/pdf")
