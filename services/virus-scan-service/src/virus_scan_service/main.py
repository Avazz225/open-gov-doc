import hashlib
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from virus_scan_service import repository
from virus_scan_service.document_client import DocumentClient, DocumentServiceError
from virus_scan_service.engines import build_engine as build_scan_engine
from virus_scan_service.models import Base
from virus_scan_service.schemas import QuarantineReleaseRequest, ScanResultOut
from virus_scan_service.settings import Settings
from virus_scan_service.storage_client import ObjectNotFoundError, StorageClient

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


def _compute_checksum(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS virus_scan"))
        await conn.run_sync(Base.metadata.create_all)
        # Quarantäne-Bereich (2.5, P15-S2): `create_all` legt neue Spalten auf
        # einer bereits existierenden Tabelle nicht nachträglich an (kein
        # Alembic in diesem Projekt, siehe document-service main.py für
        # dasselbe Idiom) - idempotente ADD COLUMN IF NOT EXISTS-Migration.
        await conn.execute(
            text(
                "ALTER TABLE virus_scan.scan_result "
                "ADD COLUMN IF NOT EXISTS resolved_by VARCHAR(128)"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE virus_scan.scan_result "
                "ADD COLUMN IF NOT EXISTS resolved_at TIMESTAMPTZ"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.storage = StorageClient(settings.storage_service_base_url)
    app.state.documents = DocumentClient(settings.document_service_base_url)
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

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    if registration:
        await registration.stop()
    await event_bus.close()
    await app.state.storage.close()
    await app.state.documents.close()
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
        actor=created_by,
    )
    return result


@app.get("/scans/{scan_id}", response_model=ScanResultOut)
async def get_scan(scan_id: str, session: AsyncSession = Depends(get_session)) -> ScanResultOut:
    try:
        return await repository.get_scan_result(session, scan_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _has_quarantine_role(x_dms_roles: str) -> bool:
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return settings.quarantine_admin_role in roles


@app.get("/scans", response_model=list[ScanResultOut])
async def list_scans(
    document_id: str | None = None,
    status: str | None = None,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[ScanResultOut]:
    """Ohne `status` bzw. mit `status != "infected"` unverändertes Verhalten
    (kein Auth-Check) - bestehende Aufrufer (z. B. eine Scan-Status-Anzeige
    im Dokumentenkontext) bleiben unberührt. `status="infected"` ist die
    Quarantäne-Einsicht (2.5, P15-S2, Konzept wörtlich: "eine eigene, eng
    begrenzte Rolle darf einen Quarantäne-Fall einsehen") und daher
    rollen-gegated, additiv wie die Papierkorb-Familie (P15-S1)."""
    if status == "infected":
        if not x_dms_principal:
            raise HTTPException(status_code=401, detail="X-DMS-Principal fehlt")
        if not _has_quarantine_role(x_dms_roles):
            raise HTTPException(
                status_code=403,
                detail=f"Nur die Rolle {settings.quarantine_admin_role!r} darf den "
                "Quarantäne-Bereich einsehen",
            )
    return await repository.list_scan_results(session, document_id=document_id, status=status)


@app.post("/scans/{scan_id}/release", response_model=ScanResultOut)
async def release_scan(
    scan_id: str,
    body: QuarantineReleaseRequest,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ScanResultOut:
    """Freigabe nach Klärung eines Fehlalarms (2.5/10.3) - legt über den
    internen Anlage-Pfad des Document Service (kein erneuter Scan, siehe
    `document_client.py`) ein neues, echtes Dokument aus den quarantänierten
    Bytes an. `folder_id`/`title`/`object_type_id`/`attributes` waren beim
    ursprünglichen (gescheiterten) Upload nie bekannt - nie wurde ja ein
    Dokument angelegt - und müssen daher hier nachgereicht werden. Ein
    auditierter Vorgang (5.3): `virus_scan.released` wird unabhängig vom
    Erfolg des Document-Service-Aufrufs NICHT publiziert, wenn die Anlage
    scheitert - keine stille Wiederherstellung bei nur teilweisem Erfolg."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="X-DMS-Principal fehlt")
    if not _has_quarantine_role(x_dms_roles):
        raise HTTPException(
            status_code=403,
            detail=f"Nur die Rolle {settings.quarantine_admin_role!r} darf einen "
            "Quarantäne-Fall freigeben",
        )
    try:
        scan = await repository.get_scan_result(session, scan_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if scan.status != "infected":
        raise HTTPException(
            status_code=409,
            detail=f"Scan befindet sich nicht in Quarantäne (status={scan.status!r})",
        )
    if scan.quarantine_object_key is None:
        raise HTTPException(status_code=409, detail="Kein quarantänierter Inhalt vorhanden")

    try:
        data = await app.state.storage.download(scan.quarantine_object_key)
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail="Quarantänierter Inhalt im Storage Service nicht (mehr) vorhanden",
        ) from exc
    try:
        document = await app.state.documents.create_from_quarantine_release(
            data=data,
            filename=scan.filename,
            content_type=scan.content_type,
            title=body.title,
            created_by=x_dms_principal,
            folder_id=body.folder_id,
            object_type_id=body.object_type_id,
            attributes=body.attributes,
            source_scan_id=scan_id,
            x_dms_principal=x_dms_principal,
            x_dms_roles=x_dms_roles,
        )
    except DocumentServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    await app.state.storage.delete(scan.quarantine_object_key)
    scan = await repository.mark_resolved(
        session,
        scan_id,
        new_status="released",
        resolved_by=x_dms_principal,
        document_id=document["id"],
    )
    await session.commit()
    await publish_event(
        "virus_scan.released",
        subject=scan_id,
        payload={
            "document_id": document["id"],
            "filename": scan.filename,
            "released_by": x_dms_principal,
        },
        actor=x_dms_principal,
    )
    return scan


@app.post("/scans/{scan_id}/purge", status_code=204)
async def purge_scan(
    scan_id: str,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Endgültige Löschung eines Quarantäne-Falls (2.5) - entfernt nur die
    quarantänierten Bytes aus dem Storage Service, die `ScanResult`-Zeile
    selbst bleibt (mit status="purged") als Nachweis erhalten, analog zum
    `DeletionRegisterEntry`-Muster der Papierkorb-Familie (P15-S1)."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="X-DMS-Principal fehlt")
    if not _has_quarantine_role(x_dms_roles):
        raise HTTPException(
            status_code=403,
            detail=f"Nur die Rolle {settings.quarantine_admin_role!r} darf einen "
            "Quarantäne-Fall endgültig löschen",
        )
    try:
        scan = await repository.get_scan_result(session, scan_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if scan.status != "infected":
        raise HTTPException(
            status_code=409,
            detail=f"Scan befindet sich nicht in Quarantäne (status={scan.status!r})",
        )

    if scan.quarantine_object_key is not None:
        await app.state.storage.delete(scan.quarantine_object_key)
    await repository.mark_resolved(
        session, scan_id, new_status="purged", resolved_by=x_dms_principal
    )
    await session.commit()
    await publish_event(
        "virus_scan.purged",
        subject=scan_id,
        payload={
            "filename": scan.filename,
            "threat_name": scan.threat_name,
            "purged_by": x_dms_principal,
        },
        actor=x_dms_principal,
    )
