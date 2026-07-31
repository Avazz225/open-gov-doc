import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response
from ocr_service import repository
from ocr_service.consumer import start_consuming
from ocr_service.document_client import DocumentServiceClient
from ocr_service.models import Base
from ocr_service.schemas import OcrConfigIn, OcrConfigOut, OcrResultOut
from ocr_service.settings import Settings
from ocr_service.storage_client import StorageClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS ocr"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc-Schema-Erweiterung (kein Alembic in dieser frühen Phase, siehe
        # CONTRIBUTING.md): `create_all` legt fehlende TABELLEN an, ändert aber
        # keine bestehenden - `allowed_content_types` kam erst in P5d-S1 dazu
        # (zunächst als Negativliste `excluded_content_types` gebaut, noch in
        # derselben Session auf eine Positivliste korrigiert - `DROP` betrifft
        # daher keine echten Admin-Einstellungen).
        await conn.execute(
            text("ALTER TABLE ocr.ocr_config DROP COLUMN IF EXISTS excluded_content_types")
        )
        await conn.execute(
            text(
                "ALTER TABLE ocr.ocr_config "
                "ADD COLUMN IF NOT EXISTS allowed_content_types JSON DEFAULT '[]'::json NOT NULL"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.document_client = DocumentServiceClient(settings.document_service_base_url)
    app.state.storage = StorageClient(settings.storage_service_base_url)

    # Reiner Konsument fremder Streams (`document.>`, ensure_stream=False) und
    # eigener Producer-Client (eigener Stream "ocr") getrennt, wie bei
    # rendering-service - ein Producer muss seinen Stream selbst anlegen,
    # siehe ADR 0001.
    event_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await event_bus.connect()
    app.state.event_bus = event_bus

    publisher = NatsEventBusClient(settings.nats_url, stream="ocr", ensure_stream=True)
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
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(event_type: str, subject: str, payload: dict) -> None:
    event = Event(
        event_type=event_type, service_name=settings.service_name, subject=subject, payload=payload
    )
    await app.state.publisher.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/config", response_model=OcrConfigOut)
async def get_config(session: AsyncSession = Depends(get_session)) -> OcrConfigOut:
    config = await repository.get_config(session)
    await session.commit()
    return config


@app.put("/config", response_model=OcrConfigOut)
async def put_config(
    body: OcrConfigIn, session: AsyncSession = Depends(get_session)
) -> OcrConfigOut:
    config = await repository.update_config(
        session,
        max_word_count=body.max_word_count,
        batch_size=body.batch_size,
        allowed_content_types=body.allowed_content_types,
    )
    await session.commit()
    return config


@app.get("/ocr-results", response_model=list[OcrResultOut])
async def list_ocr_results(
    document_id: str,
    version_number: int | None = None,
    session: AsyncSession = Depends(get_session),
) -> list[OcrResultOut]:
    return await repository.list_ocr_results(
        session, document_id=document_id, version_number=version_number
    )


@app.get("/ocr-results/{ocr_result_id}", response_model=OcrResultOut)
async def get_ocr_result(
    ocr_result_id: str, session: AsyncSession = Depends(get_session)
) -> OcrResultOut:
    try:
        return await repository.get_ocr_result(session, ocr_result_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/ocr-results/{ocr_result_id}/page-image")
async def download_page_image(
    ocr_result_id: str, session: AsyncSession = Depends(get_session)
) -> Response:
    try:
        result = await repository.get_ocr_result(session, ocr_result_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result.page_image_storage_key is None:
        raise HTTPException(
            status_code=409,
            detail="Kein eigenständiges Seitenbild für dieses Ergebnis "
            "(Rasterbild - siehe rendering-service-Thumbnail)",
        )
    data = await app.state.storage.download(result.page_image_storage_key)
    return Response(content=data, media_type="image/png")
