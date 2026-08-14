import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_permission_client import PermissionServiceClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from ocr_service import repository
from ocr_service.consumer import start_consuming
from ocr_service.document_client import DocumentServiceClient
from ocr_service.models import Base, OcrResult
from ocr_service.pipeline import process_version
from ocr_service.schemas import OcrConfigIn, OcrConfigOut, OcrResultOut
from ocr_service.settings import Settings
from ocr_service.storage_client import StorageClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def _run_retry_tick(session_factory) -> None:
    """Ein einzelner Durchlauf der fälligen OCR-Wiederholungsversuche
    (Post-Roadmap Phase 20 Session 4, ADR 0080) - ausgelagert aus
    `_ocr_retry_poll_loop`, damit ein Tick isoliert testbar ist. Ruft
    `pipeline.process_version` erneut auf (nicht nur einen Teilschritt) - es
    gibt nur EIN autoritatives Ergebnis je Version, ein Neudurchlauf ist
    idempotent (`upsert_ocr_result` überschreibt dieselbe Zeile)."""
    async with session_factory() as session:
        due = await repository.list_due_for_retry(session)
    for stale in due:
        async with session_factory() as session:
            fresh = await session.get(OcrResult, stale.id)
            if fresh is None or fresh.status != "failed":
                continue  # zwischenzeitlich anders behandelt (z. B. manueller Retry)
        await process_version(
            fresh.document_id,
            fresh.version_number,
            session_factory=session_factory,
            document_client=app.state.document_client,
            storage=app.state.storage,
            publish_event=publish_event,
            max_attempts=settings.max_ocr_attempts,
        )


async def _ocr_retry_poll_loop(session_factory) -> None:
    """Wiederholt fehlgeschlagene OCR-Verarbeitung (Post-Roadmap Phase 20
    Session 4, ADR 0080) - der erste Versuch bleibt synchron im NATS-Handler,
    nur die WIEDERHOLUNG läuft asynchron in diesem eigenen Poll-Loop.
    Gleiches Idiom wie notification-service's `_notification_retry_poll_loop`."""
    while True:
        try:
            await _run_retry_tick(session_factory)
        except Exception:
            logger.exception(
                "OCR-Retry-Poll-Tick fehlgeschlagen - wird beim naechsten Tick erneut versucht."
            )
        await asyncio.sleep(settings.ocr_retry_poll_interval_seconds)


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
        # Retry/Backoff (Post-Roadmap Phase 20 Session 4, ADR 0080).
        await conn.execute(
            text(
                "ALTER TABLE ocr.ocr_result "
                "ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0"
            )
        )
        await conn.execute(
            text("ALTER TABLE ocr.ocr_result ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ")
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.document_client = DocumentServiceClient(settings.document_service_base_url)
    app.state.storage = StorageClient(settings.storage_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)

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
        max_attempts=settings.max_ocr_attempts,
    )

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    retry_poll_task = asyncio.create_task(_ocr_retry_poll_loop(app.state.session_factory))

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    retry_poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await retry_poll_task
    if registration:
        await registration.stop()
    await publisher.close()
    await event_bus.close()
    await app.state.document_client.close()
    await app.state.storage.close()
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


async def _require_ocr_permission(x_dms_principal: str, *, access_type: str) -> None:
    """RBAC (Post-Roadmap Phase 19 Session 8, ADR 0073) - ocr-service hatte
    zuvor GAR KEINE Berechtigungsprüfung. Prüft `ocr.read`/`ocr.write` an
    der Wurzelressource (`root`) - ocr-service registriert keine eigenen
    Ressourcen-Baumknoten. Die "everyone"-Gruppe (ADR 0067) gewährt beide
    standardmäßig - erhält das bisherige De-facto-offene Verhalten, macht
    es aber admin-editierbar."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    permission = "ocr.read" if access_type == "read" else "ocr.write"
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=PermissionServiceClient.ROOT_RESOURCE_ID,
        permission=permission,
        access_type=access_type,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Fehlende Berechtigung {permission!r}")


@app.get("/config", response_model=OcrConfigOut)
async def get_config(
    x_dms_principal: str = Header(default=""), session: AsyncSession = Depends(get_session)
) -> OcrConfigOut:
    await _require_ocr_permission(x_dms_principal, access_type="read")
    config = await repository.get_config(session)
    await session.commit()
    return config


@app.put("/config", response_model=OcrConfigOut)
async def put_config(
    body: OcrConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> OcrConfigOut:
    await _require_ocr_permission(x_dms_principal, access_type="write")
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
    document_id: str | None = None,
    version_number: int | None = None,
    status: str | None = None,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[OcrResultOut]:
    """``document_id`` ist seit Post-Roadmap Phase 20 Session 7 optional -
    ohne ihn (typischerweise kombiniert mit ``status``) liefert dies eine
    dokumentübergreifende Liste, Grundlage für die neue Admin-UI-Sicht auf
    dauerhaft fehlgeschlagene OCR-Ergebnisse."""
    await _require_ocr_permission(x_dms_principal, access_type="read")
    return await repository.list_ocr_results(
        session, document_id=document_id, version_number=version_number, status=status
    )


@app.get("/ocr-results/{ocr_result_id}", response_model=OcrResultOut)
async def get_ocr_result(
    ocr_result_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> OcrResultOut:
    await _require_ocr_permission(x_dms_principal, access_type="read")
    try:
        return await repository.get_ocr_result(session, ocr_result_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/ocr-results/{ocr_result_id}/retry", response_model=OcrResultOut)
async def retry_ocr_result(
    ocr_result_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> OcrResultOut:
    """Manueller Neustart eines dauerhaft fehlgeschlagenen OCR-Laufs
    (Post-Roadmap Phase 20 Session 4, ADR 0080) - nur fuer `failed_permanent`
    sinnvoll (409 sonst); unternimmt sofort einen neuen synchronen Versuch
    statt auf den naechsten Poll-Tick zu warten."""
    await _require_ocr_permission(x_dms_principal, access_type="write")
    try:
        result = await repository.get_ocr_result(session, ocr_result_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if result.status != "failed_permanent":
        raise HTTPException(
            status_code=409,
            detail=(
                f"OCR-Ergebnis hat Status {result.status!r}, nur 'failed_permanent' "
                "kann erneut versucht werden"
            ),
        )
    document_id, version_number = result.document_id, result.version_number
    await repository.reset_for_retry(session, result)
    await session.commit()
    await process_version(
        document_id,
        version_number,
        session_factory=app.state.session_factory,
        document_client=app.state.document_client,
        storage=app.state.storage,
        publish_event=publish_event,
        max_attempts=settings.max_ocr_attempts,
    )
    # Frische Session statt der obigen `session` (deren Identity Map sonst die
    # VOR `process_version` geladene, jetzt veraltete Instanz zurückgäbe -
    # `process_version` committet über eigene, separate Sessions).
    async with app.state.session_factory() as fresh_session:
        return await repository.get_ocr_result(fresh_session, ocr_result_id)


@app.get("/ocr-results/{ocr_result_id}/page-image")
async def download_page_image(
    ocr_result_id: str,
    page_number: int = 1,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _require_ocr_permission(x_dms_principal, access_type="read")
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
    if page_number < 1 or page_number > len(result.pages):
        raise HTTPException(status_code=404, detail=f"Seite {page_number} existiert nicht")
    # `page_image_storage_key` ist seit dem Mehrseiten-Bugfix ein Präfix ohne
    # Seitensuffix, siehe `pipeline.process_version`.
    data = await app.state.storage.download(f"{result.page_image_storage_key}-{page_number}.png")
    return Response(content=data, media_type="image/png")
