import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from archival_service import crypto, pipeline, repository
from archival_service.clients import (
    DocumentClient,
    ObjectTypeClient,
    RenderingClient,
    StorageClient,
)
from archival_service.keystore import EnvKeyStore, KeyNotFoundError
from archival_service.models import Base
from archival_service.schemas import ArchivalTransferOut
from archival_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def _archival_poll_loop(session_factory) -> None:
    """Faelligkeits-Poll fuer Aussonderung/Dehydrierung (5.6) - gleiches
    Idiom wie document-service's `_retention_poll_loop`/reporting-service's
    `_report_schedule_poll_loop`: zwei unabhaengige Phasen je Durchlauf, ein
    Fehler in einer Phase bricht die Schleife nicht ab."""
    while True:
        try:
            await pipeline.run_active_transfers_tick(
                session_factory,
                document_client=app.state.document_client,
                rendering_client=app.state.rendering_client,
                object_type_client=app.state.object_type_client,
                storage_client=app.state.storage_client,
                keystore=app.state.keystore,
            )
        except Exception:
            logger.exception(
                "Aussonderungs-Poll-Tick fehlgeschlagen - wird beim naechsten Tick erneut versucht."
            )

        try:
            await pipeline.run_dehydration_tick(
                session_factory,
                document_client=app.state.document_client,
                storage_client=app.state.storage_client,
                delay_days=settings.dehydration_delay_days,
            )
        except Exception:
            logger.exception(
                "Dehydrierungs-Poll-Tick fehlgeschlagen - wird beim naechsten Tick erneut versucht."
            )

        await asyncio.sleep(settings.archival_poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS archival"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.document_client = DocumentClient(settings.document_service_base_url)
    app.state.rendering_client = RenderingClient(settings.rendering_service_base_url)
    app.state.storage_client = StorageClient(settings.storage_service_base_url)
    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)
    app.state.keystore = EnvKeyStore(settings.archive_encryption_key)

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    poll_task = asyncio.create_task(_archival_poll_loop(app.state.session_factory))

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await poll_task
    if registration:
        await registration.stop()
    await app.state.document_client.close()
    await app.state.rendering_client.close()
    await app.state.storage_client.close()
    await app.state.object_type_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/archival-transfers", response_model=list[ArchivalTransferOut])
async def list_archival_transfers(
    status: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[ArchivalTransferOut]:
    return await repository.list_transfers(session, status=status)


@app.get("/archival-transfers/{transfer_id}", response_model=ArchivalTransferOut)
async def get_archival_transfer(
    transfer_id: str, session: AsyncSession = Depends(get_session)
) -> ArchivalTransferOut:
    try:
        return await repository.get_transfer(session, transfer_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/archival-transfers/{transfer_id}/retrieve", response_model=ArchivalTransferOut)
async def retrieve_archival_transfer(
    transfer_id: str,
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ArchivalTransferOut:
    """Rueckholung (5.6, wörtliche Konzeptvorgabe: "ein eigener, ebenfalls
    auditierter Vorgang mit Entschlüsselung nur für berechtigte Rollen") -
    schreibt den entschluesselten Archiv-Inhalt zurueck auf die Live-Ziele
    unter genau demselben Storage-Schluessel wie die aktuelle Version, damit
    der reguläre `document-service`-Download-Pfad danach unveraendert
    funktioniert."""
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    if settings.archive_retrieval_role not in roles:
        raise HTTPException(
            status_code=403,
            detail=f"Rolle {settings.archive_retrieval_role!r} erforderlich für die Rückholung",
        )
    try:
        transfer = await repository.get_transfer(session, transfer_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if transfer.status not in ("released", "dehydrated"):
        raise HTTPException(
            status_code=409,
            detail=f"Transfer hat Status {transfer.status!r} - keine Archivkopie zum Zurückholen",
        )

    data = await app.state.storage_client.download_archive_copy(transfer.storage_object_key)
    if transfer.encrypted:
        try:
            key = app.state.keystore.get_key("default")
        except KeyNotFoundError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        try:
            data = crypto.decrypt(data, key)
        except crypto.DecryptionError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    document = await app.state.document_client.get_document(transfer.document_id)
    version = await app.state.document_client.get_version(
        transfer.document_id, document["current_version_number"]
    )
    await app.state.storage_client.upload(
        version["storage_object_key"], data, version["content_type"] or "application/octet-stream"
    )
    await app.state.document_client.mark_rehydrated(transfer.document_id)

    now = datetime.now(UTC)
    await repository.update_status(
        session,
        transfer,
        status="released",
        released_at=now,
        dehydrated_at=None,
        rehydrated_at=now,
    )
    await session.commit()
    return transfer
