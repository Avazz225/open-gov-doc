import hashlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storage_service import identity_guard, replication, repository, retention_guard
from storage_service.backends import (
    ObjectNotFoundError,
    S3Backend,
    build_backends,
    resolve_archive_targets,
    resolve_targets,
)
from storage_service.models import Base, TargetOverride
from storage_service.schemas import (
    FixityEntry,
    GuardConfigIn,
    GuardConfigOut,
    GuardStatusEntry,
    ObjectCopyOut,
    ObjectMetadataOut,
    OperationalConfigIn,
    OperationalConfigOut,
    ReplicationRunResult,
    StorageUsageEntry,
    TargetConfigIn,
    VerifyResult,
)
from storage_service.settings import BackendTargetConfig, Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


def _compute_target_state(
    targets: list[BackendTargetConfig], overrides: dict[str, TargetOverride]
) -> tuple[list[BackendTargetConfig], list[str], list[str], set[str]]:
    """Merged die Env-Var-Ziele mit den in der DB gespeicherten
    `TargetOverride`-Zeilen (Post-Roadmap Phase 22 Session 7, ADR 0092) -
    Zugangsdaten/Struktur kommen unverändert aus `targets`, nur
    `object_lock_mode`/`role` können überschrieben sein. Aufrufer (Startup
    UND jeder `PUT /guard-status/{id}/config`) schreiben das Ergebnis direkt
    in `app.state` zurück, damit der übrige Code weiterhin einfache
    `app.state`-Lookups nutzt, ohne bei jedem einzelnen Request selbst neu
    aus der DB zu lesen."""
    effective = [
        target.model_copy(
            update={
                "object_lock_mode": (
                    overrides[target.id].object_lock_mode
                    if target.id in overrides
                    else target.object_lock_mode
                ),
                "role": overrides[target.id].role if target.id in overrides else target.role,
            }
        )
        for target in targets
    ]
    target_ids = resolve_targets(effective)
    archive_ids = resolve_archive_targets(effective)
    lock_ids = {t.id for t in effective if t.object_lock_mode is not None}
    return effective, target_ids, archive_ids, lock_ids


def _validate_settings(settings: Settings) -> None:
    targets = resolve_targets(settings.targets)
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
    startup_start = time.time()
    _validate_settings(settings)

    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS storage"))
        await conn.run_sync(Base.metadata.create_all)
        # Aufbewahrung/WORM (5.1/5.2a, P7-S1) - Ad-hoc-Migrationsmuster wie
        # in jedem anderen Service dieser Phase (kein Alembic).
        await conn.execute(
            text(
                "ALTER TABLE storage.object_copy "
                "ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ"
            )
        )
        # Full-Jitter-Backoff für die Retry-Queue (Post-Roadmap Phase 20
        # Session 6, ADR 0082) - gleiches Ad-hoc-Migrationsmuster.
        await conn.execute(
            text(
                "ALTER TABLE storage.object_copy ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    # Aussonderung (5.6, seit P7-S3) - Archiv-Ziele sind NICHT Teil von
    # `app.state.targets` (reguläre Upload-Replikation), sondern nur über die
    # neuen `.../archive-copy`-Endpunkte erreichbar. Seit Post-Roadmap Phase
    # 22 Session 7 (ADR 0092) werden `object_lock_mode`/`role` zusätzlich mit
    # etwaigen `TargetOverride`-Zeilen gemergt, bevor diese vier Listen
    # berechnet werden - siehe `_compute_target_state`.
    async with app.state.session_factory() as session:
        target_overrides = {o.target_id: o for o in await repository.list_target_overrides(session)}
    (
        app.state.target_configs,
        app.state.targets,
        app.state.archive_targets,
        app.state.lock_target_ids,
    ) = _compute_target_state(settings.targets, target_overrides)
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

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    if registration:
        await registration.stop()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def _get_operational_config(session: AsyncSession):
    """Frisch aus der DB gelesen bei jedem Aufruf (kein `app.state`-Cache) -
    Post-Roadmap Phase 22 Session 6, ADR 0091: macht `write_strategy`/
    `quorum_count`/`max_replication_attempts` ohne Neustart wirksam, gleiches
    Live-Reload-Prinzip wie `GuardConfig`/`ocr_service.OcrConfig`."""
    return await repository.get_operational_config(
        session,
        default_write_strategy=settings.write_strategy,
        default_quorum_count=settings.quorum_count,
        default_max_replication_attempts=settings.max_replication_attempts,
    )


@app.get("/healthz")
def healthz() -> dict:
    targets = resolve_targets(settings.targets)
    return {
        "status": "ok",
        "service": settings.service_name,
        "primary_target": targets[0],
        "targets": targets,
        "write_strategy": settings.write_strategy,
    }


@app.get("/objects/{key:path}/copies", response_model=list[ObjectCopyOut])
async def list_object_copies(
    key: str, session: AsyncSession = Depends(get_session)
) -> list[ObjectCopyOut]:
    return await repository.list_copies(session, key)


@app.put("/objects/{key:path}/archive-copy", response_model=ObjectMetadataOut, status_code=201)
async def upload_archive_copy(
    key: str, request: Request, session: AsyncSession = Depends(get_session)
) -> ObjectMetadataOut:
    """Aussonderung (5.6, seit P7-S3): schreibt NUR auf die konfigurierten
    Archiv-Ziele (`role="archive"`), nicht auf die reguläre Live-Ziel-Menge -
    aufgerufen von `archival-service`, nicht Teil des normalen Upload-Pfads.
    `key` ist bewusst unabhängig vom Live-Objektschlüssel des Dokuments (die
    Archivkopie ist inhaltlich ein anderes Objekt, i. d. R. die PDF/A-
    Rendition statt des Originals)."""
    if not app.state.archive_targets:
        raise HTTPException(
            status_code=503,
            detail="Kein Archiv-Ziel konfiguriert (BackendTargetConfig.role=archive)",
        )
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Leerer Request-Body")

    checksum = hashlib.sha256(data).hexdigest()
    metadata = await repository.upsert_metadata(
        session,
        object_key=key,
        backend=app.state.archive_targets[0],
        checksum_sha256=checksum,
        size_bytes=len(data),
        content_type=request.headers.get("content-type"),
    )
    try:
        await replication.write_to_targets(
            session,
            backends=app.state.backends,
            targets=app.state.archive_targets,
            key=key,
            data=data,
            checksum=checksum,
        )
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=502, detail=f"Schreiben auf Archiv-Ziel fehlgeschlagen: {exc}"
        ) from exc

    await session.commit()
    return metadata


@app.get("/objects/{key:path}/archive-copy")
async def download_archive_copy(key: str, session: AsyncSession = Depends(get_session)) -> Response:
    """Rückholung (5.6, seit P7-S3) - liest ausschließlich von Archiv-
    Zielen, unabhängig vom Live-Zustand desselben Objektschlüssels."""
    try:
        metadata = await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc

    try:
        data = await replication.read_with_fallback(
            session, backends=app.state.backends, targets=app.state.archive_targets, key=key
        )
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404, detail="Objekt in keinem konfigurierten Archiv-Ziel (mehr) vorhanden"
        ) from exc

    return Response(content=data, media_type=metadata.content_type or "application/octet-stream")


@app.get("/objects/{key:path}/archive-copy/verify", response_model=list[FixityEntry])
async def verify_archive_copy(
    key: str, session: AsyncSession = Depends(get_session)
) -> list[FixityEntry]:
    """Fixity-Check der Archiv-Kopie (5.6, seit P7-S3) - wiederverwendet
    dieselbe Verifikationslogik wie `GET /object-verify/{key}/all`, gefiltert
    auf Archiv-Ziele."""
    try:
        metadata = await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc

    results = await replication.verify_all_copies(
        session, backends=app.state.backends, key=key, expected_checksum=metadata.checksum_sha256
    )
    await session.commit()
    return [r for r in results if r["backend_id"] in app.state.archive_targets]


@app.delete("/objects/{key:path}/live-copies", status_code=204)
async def delete_live_copies(
    key: str,
    bypass_governance: bool = False,
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """ "Dehydrieren" (5.6, seit P7-S3): entfernt die Kopie(n) auf den
    regulären Live-Zielen, NICHT auf Archiv-Zielen - das unterscheidet dies
    bewusst von `DELETE /objects/{key}`, das wirklich alle Kopien entfernt.
    Gleiches Governance-Lock-Gate wie die reguläre Löschung."""
    try:
        await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc

    locked_targets = await retention_guard.find_locked_targets(
        session, key, targets=app.state.target_configs
    )
    if locked_targets:
        if not bypass_governance:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Objekt ist unter Governance-Mode-Aufbewahrung gesperrt "
                    f"(Ziele: {locked_targets}) - Dehydrieren erfordert "
                    "?bypass_governance=true mit passender Rolle"
                ),
            )
        if not retention_guard.has_governance_bypass_role(x_dms_roles, settings):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Rolle {settings.governance_bypass_role!r} erforderlich, um eine "
                    f"Governance-Mode-Sperre zu umgehen (Ziele: {locked_targets})"
                ),
            )

    await replication.delete_from_targets(
        session,
        backends=app.state.backends,
        targets=app.state.targets,
        key=key,
        bypass_governance=bool(locked_targets) and bypass_governance,
    )
    await session.commit()


@app.put("/objects/{key:path}", response_model=ObjectMetadataOut, status_code=201)
async def upload_object(
    key: str,
    request: Request,
    retain_until: datetime | None = None,
    session: AsyncSession = Depends(get_session),
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

    operational_config = await _get_operational_config(session)
    try:
        await replication.write_with_redundancy(
            session,
            backends=app.state.backends,
            targets=app.state.targets,
            strategy=operational_config.write_strategy,
            quorum_count=operational_config.quorum_count,
            key=key,
            data=data,
            checksum=checksum,
            retention_until=retain_until,
            lock_target_ids=app.state.lock_target_ids,
        )
    except replication.PrimaryWriteError as exc:
        await session.rollback()
        raise HTTPException(status_code=502, detail=f"Primärziel nicht erreichbar: {exc}") from exc
    except replication.QuorumNotReachedError as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    await session.commit()
    return metadata


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
async def delete_object(
    key: str,
    bypass_governance: bool = False,
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    try:
        await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc

    # WORM/Object-Lock-Guard (5.1/5.2a, seit P7-S1): blockiert die Löschung,
    # solange mindestens ein Ziel mit aktivem `object_lock_mode` noch eine in
    # der Zukunft liegende Aufbewahrungsfrist für dieses Objekt hat - außer,
    # der Aufrufer fordert `bypass_governance=true` UND hat die konfigurierte
    # Rolle (Default `dms-admin`, siehe `settings.governance_bypass_role`).
    locked_targets = await retention_guard.find_locked_targets(
        session, key, targets=app.state.target_configs
    )
    if locked_targets:
        if not bypass_governance:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Objekt ist unter Governance-Mode-Aufbewahrung gesperrt "
                    f"(Ziele: {locked_targets}) - Löschung erfordert "
                    "?bypass_governance=true mit passender Rolle"
                ),
            )
        if not retention_guard.has_governance_bypass_role(x_dms_roles, settings):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Rolle {settings.governance_bypass_role!r} erforderlich, um eine "
                    f"Governance-Mode-Sperre zu umgehen (Ziele: {locked_targets})"
                ),
            )

    await replication.delete_from_all(
        session,
        backends=app.state.backends,
        targets=app.state.targets,
        key=key,
        bypass_governance=bool(locked_targets) and bypass_governance,
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


@app.get("/storage/usage", response_model=list[StorageUsageEntry])
async def get_storage_usage(
    session: AsyncSession = Depends(get_session),
) -> list[StorageUsageEntry]:
    """Speicherverbrauch je Backend (5.4a, seit P7-S2b) - Grundlage für den
    gleichnamigen Standardbericht im Reporting Service."""
    rows = await repository.get_storage_usage(session)
    return [
        StorageUsageEntry(backend=backend, object_count=count, total_size_bytes=total_size)
        for backend, count, total_size in rows
    ]


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
    operational_config = await _get_operational_config(session)
    result = await replication.process_pending(
        session,
        backends=app.state.backends,
        max_attempts=operational_config.max_replication_attempts,
        limit=limit,
    )
    await session.commit()
    return result


@app.get("/operational-config", response_model=OperationalConfigOut)
async def get_operational_config(
    session: AsyncSession = Depends(get_session),
) -> OperationalConfigOut:
    return await _get_operational_config(session)


@app.put("/operational-config", response_model=OperationalConfigOut)
async def put_operational_config(
    body: OperationalConfigIn, session: AsyncSession = Depends(get_session)
) -> OperationalConfigOut:
    """Betriebsparameter (Post-Roadmap Phase 22 Session 6, ADR 0091) - anders
    als das Ziel-Set selbst (Zugangsdaten, `Settings.targets`, bewusst
    weiterhin env-var-only) ohne Geheimnisse, daher live editierbar. Die
    Anzahl konfigurierter Ziele ist strukturell fest (env-var, diese Session
    ändert daran nichts) - dieselbe Quorum-Erfüllbarkeits-Prüfung wie beim
    Start (`_validate_settings`), hier gegen einen admin-gewählten Wert statt
    des Env-Var-Defaults."""
    if body.write_strategy == "quorum" and not (1 <= body.quorum_count <= len(app.state.targets)):
        raise HTTPException(
            status_code=422,
            detail=(
                f"quorum_count={body.quorum_count} ist mit {len(app.state.targets)} "
                "konfigurierten Ziel(en) nicht erfüllbar"
            ),
        )
    config = await repository.update_operational_config(
        session,
        write_strategy=body.write_strategy,
        quorum_count=body.quorum_count,
        max_replication_attempts=body.max_replication_attempts,
    )
    await session.commit()
    return config


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


@app.post("/guard-status/{target_id}/reidentify", response_model=GuardStatusEntry)
async def reidentify_target(
    target_id: str, session: AsyncSession = Depends(get_session)
) -> GuardStatusEntry:
    """Korrekturmechanismus für einen beabsichtigten Datenträger-Wechsel
    (3.6, P5c-S2, ADR 0017-Folgepunkt) - ersetzt die bisher nötige direkte
    Korrektur in `backend_identity` durch einen API-Aufruf, ohne Neustart.
    Markiert alle bisherigen Kopien des Ziels zur Nachreplikation, wie beim
    automatischen degradierten Start (`POST /replication/process-pending`
    zieht sie nach)."""
    if target_id not in app.state.targets:
        raise HTTPException(status_code=404, detail=f"Unbekanntes Ziel: {target_id!r}")

    try:
        identity = await identity_guard.reidentify_target(
            session, target_id, app.state.backends[target_id]
        )
    except Exception as exc:
        await session.rollback()
        raise HTTPException(
            status_code=502, detail=f"Ziel {target_id!r} nicht erreichbar: {exc}"
        ) from exc

    await session.commit()
    pending_counts = await repository.count_pending_copies_by_backend(session)
    configs = {t.id: t for t in app.state.target_configs}
    return GuardStatusEntry(
        target_id=target_id,
        device_id=identity.device_id,
        verified_at=identity.verified_at,
        pending_copies=pending_counts.get(target_id, 0),
        object_lock_mode=configs[target_id].object_lock_mode if target_id in configs else None,
        role=configs[target_id].role if target_id in configs else None,
    )


@app.get("/guard-status", response_model=list[GuardStatusEntry])
async def get_guard_status(session: AsyncSession = Depends(get_session)) -> list[GuardStatusEntry]:
    """Admin-UI-Statusblock (3.6 "im Admin-UI als Status sichtbar", P5b-S6):
    zuletzt bestätigte Geräte-ID je konfiguriertem Ziel plus Anzahl noch
    nicht replizierter Kopien - ein Ziel mit `pending_copies > 0` nach einem
    degradierten Start befindet sich noch in der Wiederherstellung."""
    identities = {i.target_id: i for i in await repository.list_backend_identities(session)}
    pending_counts = await repository.count_pending_copies_by_backend(session)
    configs = {t.id: t for t in app.state.target_configs}
    return [
        GuardStatusEntry(
            target_id=target_id,
            device_id=identities[target_id].device_id if target_id in identities else None,
            verified_at=identities[target_id].verified_at if target_id in identities else None,
            pending_copies=pending_counts.get(target_id, 0),
            object_lock_mode=configs[target_id].object_lock_mode if target_id in configs else None,
            role=configs[target_id].role if target_id in configs else None,
        )
        for target_id in app.state.targets
    ]


@app.put("/guard-status/{target_id}/config", response_model=GuardStatusEntry)
async def put_target_config(
    target_id: str, body: TargetConfigIn, session: AsyncSession = Depends(get_session)
) -> GuardStatusEntry:
    """Ziel-Metadaten live editieren (Post-Roadmap Phase 22 Session 7,
    ADR 0092) - NUR `object_lock_mode`/`role` je bereits konfiguriertem Ziel
    ("nur bestehende Einträge bearbeiten", gleiche Vorgabe wie P22-S6).
    `404` bei unbekannter `target_id` (die Ziel-*Liste* selbst bleibt
    env-var-only, keine neuen IDs über diesen Endpunkt). Schreibt das
    Ergebnis sofort in `app.state` zurück (`_compute_target_state`), wirkt
    also ohne Neustart auf jeden nachfolgenden Request."""
    if target_id not in {t.id for t in settings.targets}:
        raise HTTPException(status_code=404, detail=f"Unbekanntes Ziel: {target_id!r}")

    existing_overrides = {o.target_id: o for o in await repository.list_target_overrides(session)}
    would_be_overrides = dict(existing_overrides)
    would_be_overrides[target_id] = TargetOverride(
        target_id=target_id, object_lock_mode=body.object_lock_mode, role=body.role
    )
    _, would_be_targets, _, _ = _compute_target_state(settings.targets, would_be_overrides)
    if not would_be_targets:
        raise HTTPException(
            status_code=422,
            detail=(
                f"role={body.role!r} für {target_id!r} würde kein reguläres Ziel mehr übrig "
                "lassen (mindestens eines muss außerhalb role=archive bleiben)"
            ),
        )

    await repository.upsert_target_override(
        session, target_id, object_lock_mode=body.object_lock_mode, role=body.role
    )
    await session.commit()

    target_overrides = {o.target_id: o for o in await repository.list_target_overrides(session)}
    (
        app.state.target_configs,
        app.state.targets,
        app.state.archive_targets,
        app.state.lock_target_ids,
    ) = _compute_target_state(settings.targets, target_overrides)

    identities = {i.target_id: i for i in await repository.list_backend_identities(session)}
    pending_counts = await repository.count_pending_copies_by_backend(session)
    configs = {t.id: t for t in app.state.target_configs}
    return GuardStatusEntry(
        target_id=target_id,
        device_id=identities[target_id].device_id if target_id in identities else None,
        verified_at=identities[target_id].verified_at if target_id in identities else None,
        pending_copies=pending_counts.get(target_id, 0),
        object_lock_mode=configs[target_id].object_lock_mode if target_id in configs else None,
        role=configs[target_id].role if target_id in configs else None,
    )
