import hashlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storage_service import identity_guard, replication, repository
from storage_service.backends import ObjectNotFoundError, S3Backend, build_backends, resolve_targets
from storage_service.models import Base
from storage_service.schemas import (
    FixityEntry,
    GuardConfigIn,
    GuardConfigOut,
    GuardStatusEntry,
    ObjectCopyOut,
    ObjectMetadataOut,
    ReplicationRunResult,
    VerifyResult,
)
from storage_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


def _validate_settings(settings: Settings) -> None:
    targets = resolve_targets(settings)
    if not targets:
        raise RuntimeError("Mindestens ein Ziel muss konfiguriert sein")
    if len(set(targets)) != len(targets):
        raise RuntimeError(f"Ziel-`id`s müssen eindeutig sein, gefunden: {targets}")
    quorum_satisfiable = 1 <= settings.quorum_count <= len(targets)
    if settings.write_strategy == "quorum" and not quorum_satisfiable:
        raise RuntimeError(
            f"quorum_count={settings.quorum_count} ist mit {len(targets)} konfigurierten "
            f"Ziel(en) nicht erfüllbar"
        )


async def _run_startup_guard(session_factory, backends: dict, targets: list[str]) -> None:
    """Datenträger-Wechsel-Wächter (3.6, P5b-S6, ADR 0017): prüft für jedes
    konfigurierte Ziel die Geräte-Identität, bevor der Service Anfragen
    entgegennimmt. Werkseinstellung ist Startverweigerung bei jeder
    Abweichung; ein Admin-Override (`GuardConfig.allow_degraded_start`)
    erlaubt einen degradierten Start, sofern mindestens ein Ziel nachweislich
    unverändert ist - in diesem Fall werden alle Kopien der betroffenen Ziele
    automatisch zur Nachreplikation vorgemerkt (`POST
    /replication/process-pending` zieht sie nach, kein In-Prozess-
    Hintergrundtask, siehe ADR 0004)."""
    verified: dict[str, bool] = {}
    async with session_factory() as session:
        for target_id in targets:
            verified[target_id] = await identity_guard.check_target_identity(
                session, target_id, backends[target_id]
            )
        await session.commit()

    unverified = [target_id for target_id, ok in verified.items() if not ok]
    if not unverified:
        return

    async with session_factory() as session:
        guard_config = await repository.get_guard_config(session)
        await session.commit()

    verified_targets = [target_id for target_id, ok in verified.items() if ok]
    if not guard_config.allow_degraded_start or not verified_targets:
        override_state = (
            "aktiv, aber kein Ziel ist nachweislich unverändert"
            if guard_config.allow_degraded_start
            else "nicht aktiv"
        )
        raise RuntimeError(
            f"Datenträger-Identität für {unverified} nicht verifizierbar - Start verweigert. "
            f"Admin-Override 'allow_degraded_start' ist {override_state} (siehe PUT /guard-config)."
        )

    logger.warning(
        "Degradierter Start: Ziel(e) %s nicht verifiziert, %s verifiziert - "
        "Kopien der nicht verifizierten Ziele werden zur Nachreplikation vorgemerkt",
        unverified,
        verified_targets,
    )
    async with session_factory() as session:
        for target_id in unverified:
            count = await repository.reset_copies_for_backend(session, target_id)
            logger.warning("%s Kopie(n) für Ziel %r auf 'pending' zurückgesetzt", count, target_id)
        await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _validate_settings(settings)

    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS storage"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.targets = resolve_targets(settings)
    app.state.backends = build_backends(settings)
    for backend in app.state.backends.values():
        if isinstance(backend, S3Backend):
            await backend.ensure_bucket()

    await _run_startup_guard(app.state.session_factory, app.state.backends, app.state.targets)

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    yield

    if registration:
        await registration.stop()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    targets = resolve_targets(settings)
    return {
        "status": "ok",
        "service": settings.service_name,
        "primary_target": targets[0],
        "targets": targets,
        "write_strategy": settings.write_strategy,
    }


@app.put("/objects/{key:path}", response_model=ObjectMetadataOut, status_code=201)
async def upload_object(
    key: str, request: Request, session: AsyncSession = Depends(get_session)
) -> ObjectMetadataOut:
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Leerer Request-Body")

    checksum = hashlib.sha256(data).hexdigest()

    metadata = await repository.upsert_metadata(
        session,
        object_key=key,
        backend=app.state.targets[0],
        checksum_sha256=checksum,
        size_bytes=len(data),
        content_type=request.headers.get("content-type"),
    )

    try:
        await replication.write_with_redundancy(
            session,
            backends=app.state.backends,
            targets=app.state.targets,
            strategy=settings.write_strategy,
            quorum_count=settings.quorum_count,
            key=key,
            data=data,
            checksum=checksum,
        )
    except replication.PrimaryWriteError as exc:
        await session.rollback()
        raise HTTPException(status_code=502, detail=f"Primärziel nicht erreichbar: {exc}") from exc
    except replication.QuorumNotReachedError as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    await session.commit()
    return metadata


@app.get("/objects/{key:path}/copies", response_model=list[ObjectCopyOut])
async def list_object_copies(
    key: str, session: AsyncSession = Depends(get_session)
) -> list[ObjectCopyOut]:
    return await repository.list_copies(session, key)


@app.get("/objects/{key:path}")
async def download_object(key: str, session: AsyncSession = Depends(get_session)) -> Response:
    try:
        metadata = await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc

    try:
        data = await replication.read_with_fallback(
            session, backends=app.state.backends, targets=app.state.targets, key=key
        )
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Objekt in keinem konfigurierten Backend (mehr) vorhanden"
        ) from exc

    return Response(content=data, media_type=metadata.content_type or "application/octet-stream")


@app.delete("/objects/{key:path}", status_code=204)
async def delete_object(key: str, session: AsyncSession = Depends(get_session)) -> None:
    try:
        await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc

    await replication.delete_from_all(
        session, backends=app.state.backends, targets=app.state.targets, key=key
    )
    await repository.delete_metadata(session, key)
    await session.commit()


@app.get("/object-metadata/{key:path}", response_model=ObjectMetadataOut)
async def get_object_metadata(
    key: str, session: AsyncSession = Depends(get_session)
) -> ObjectMetadataOut:
    try:
        return await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc


@app.get("/object-verify/{key:path}/all", response_model=list[FixityEntry])
async def verify_object_all_copies(
    key: str, session: AsyncSession = Depends(get_session)
) -> list[FixityEntry]:
    """Fixity-Check über alle konfigurierten Ziele hinweg (3.6: "regelmäßiger
    Fixity-Check über alle Kopien") - wird noch nicht automatisch periodisch
    ausgeführt, siehe Offene Punkte in docs/services/storage-service.md."""
    try:
        metadata = await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc

    results = await replication.verify_all_copies(
        session,
        backends=app.state.backends,
        key=key,
        expected_checksum=metadata.checksum_sha256,
    )
    await session.commit()
    return results


@app.get("/object-verify/{key:path}", response_model=VerifyResult)
async def verify_object(key: str, session: AsyncSession = Depends(get_session)) -> VerifyResult:
    """Fixity-Check-Grundlage (3.6): Prüfsumme des Primärziels neu aus dem
    Backend lesen und gegen den in der Shared DB hinterlegten Referenzwert
    abgleichen. Für alle konfigurierten Ziele siehe .../all."""
    try:
        metadata = await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc

    actual = await app.state.backends[app.state.targets[0]].checksum(key)
    return VerifyResult(
        ok=actual == metadata.checksum_sha256, expected=metadata.checksum_sha256, actual=actual
    )


@app.post("/replication/process-pending", response_model=ReplicationRunResult)
async def replication_process_pending(
    limit: int = 100, session: AsyncSession = Depends(get_session)
) -> ReplicationRunResult:
    """Retry-Queue für asynchron nachzuziehende Sekundärkopien (3.6) -
    bewusst ein expliziter Endpunkt statt eines In-Prozess-Hintergrundtasks
    (siehe ADR 0004), gedacht zum periodischen Aufruf durch einen externen
    Scheduler (noch nicht Teil dieser Session)."""
    result = await replication.process_pending(
        session,
        backends=app.state.backends,
        max_attempts=settings.max_replication_attempts,
        limit=limit,
    )
    await session.commit()
    return result


@app.get("/guard-config", response_model=GuardConfigOut)
async def get_guard_config(session: AsyncSession = Depends(get_session)) -> GuardConfigOut:
    return await repository.get_guard_config(session)


@app.put("/guard-config", response_model=GuardConfigOut)
async def put_guard_config(
    body: GuardConfigIn, session: AsyncSession = Depends(get_session)
) -> GuardConfigOut:
    config = await repository.update_guard_config(
        session, allow_degraded_start=body.allow_degraded_start
    )
    await session.commit()
    return config


@app.get("/guard-status", response_model=list[GuardStatusEntry])
async def get_guard_status(session: AsyncSession = Depends(get_session)) -> list[GuardStatusEntry]:
    """Admin-UI-Statusblock (3.6 "im Admin-UI als Status sichtbar", P5b-S6):
    zuletzt bestätigte Geräte-ID je konfiguriertem Ziel plus Anzahl noch
    nicht replizierter Kopien - ein Ziel mit `pending_copies > 0` nach einem
    degradierten Start befindet sich noch in der Wiederherstellung."""
    identities = {i.target_id: i for i in await repository.list_backend_identities(session)}
    pending_counts = await repository.count_pending_copies_by_backend(session)
    return [
        GuardStatusEntry(
            target_id=target_id,
            device_id=identities[target_id].device_id if target_id in identities else None,
            verified_at=identities[target_id].verified_at if target_id in identities else None,
            pending_copies=pending_counts.get(target_id, 0),
        )
        for target_id in app.state.targets
    ]
