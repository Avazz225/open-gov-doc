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
    AzureBlobBackend,
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
    """Merges the env-var targets with the `TargetOverride` rows stored in
    the DB (Post-Roadmap Phase 22 Session 7, ADR 0092) - credentials/
    structure come unchanged from `targets`, only `object_lock_mode`/
    `role` can be overridden. Callers (startup AND every
    `PUT /guard-status/{id}/config`) write the result directly back to
    `app.state`, so the rest of the code continues to use simple
    `app.state` lookups without reading fresh from the DB on every single
    request."""
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
    """Storage device swap guard (3.6, P5b-S6, ADR 0017): checks the
    device identity for every configured target before the service
    accepts requests. The default is to refuse startup on any mismatch;
    an admin override (`GuardConfig.allow_degraded_start`) allows a
    degraded start as long as at least one target is verifiably
    unchanged - in that case, all copies on the affected targets are
    automatically queued for re-replication (`POST
    /replication/process-pending` picks them up, no in-process background
    task, see ADR 0004)."""
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
        # Retention/WORM (5.1/5.2a, P7-S1) - ad-hoc migration pattern as
        # in every other service in this phase (no Alembic).
        await conn.execute(
            text(
                "ALTER TABLE storage.object_copy "
                "ADD COLUMN IF NOT EXISTS retention_until TIMESTAMPTZ"
            )
        )
        # Full-jitter backoff for the retry queue (Post-Roadmap Phase 20
        # Session 6, ADR 0082) - same ad-hoc migration pattern.
        await conn.execute(
            text(
                "ALTER TABLE storage.object_copy ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    # Records disposal (5.6, since P7-S3) - archive targets are NOT part
    # of `app.state.targets` (regular upload replication), but reachable
    # only via the new `.../archive-copy` endpoints. Since Post-Roadmap
    # Phase 22 Session 7 (ADR 0092), `object_lock_mode`/`role` are
    # additionally merged with any `TargetOverride` rows before these four
    # lists are computed - see `_compute_target_state`.
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
        elif isinstance(backend, AzureBlobBackend):
            await backend.ensure_container()

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
    """Read fresh from the DB on every call (no `app.state` cache) - Post-
    Roadmap Phase 22 Session 6, ADR 0091: makes `write_strategy`/
    `quorum_count`/`max_replication_attempts` take effect without a
    restart, same live-reload principle as `GuardConfig`/
    `ocr_service.OcrConfig`."""
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
    """Records disposal (5.6, since P7-S3): writes ONLY to the configured
    archive targets (`role="archive"`), not to the regular live target
    set - called by `archival-service`, not part of the normal upload
    path. `key` is deliberately independent of the document's live object
    key (the archive copy is a different object in content, typically the
    PDF/A rendition instead of the original)."""
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
    """Retrieval (5.6, since P7-S3) - reads exclusively from archive
    targets, independent of the live state of the same object key."""
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
    """Fixity check of the archive copy (5.6, since P7-S3) - reuses the
    same verification logic as `GET /object-verify/{key}/all`, filtered to
    archive targets."""
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
    """"Dehydrating" (5.6, since P7-S3): removes the copy/copies on the
    regular live targets, NOT on archive targets - this deliberately
    distinguishes it from `DELETE /objects/{key}`, which removes really
    all copies. Same governance-lock gate as the regular deletion."""
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

    # WORM/Object Lock guard (5.1/5.2a, since P7-S1): blocks deletion as
    # long as at least one target with active `object_lock_mode` still has
    # a retention period lying in the future for this object - unless the
    # caller requests `bypass_governance=true` AND has the configured role
    # (default `dms-admin`, see `settings.governance_bypass_role`).
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
    """Storage usage per backend (5.4a, since P7-S2b) - basis for the
    identically named standard report in the Reporting Service."""
    rows = await repository.get_storage_usage(session)
    return [
        StorageUsageEntry(backend=backend, object_count=count, total_size_bytes=total_size)
        for backend, count, total_size in rows
    ]


@app.get("/object-verify/{key:path}/all", response_model=list[FixityEntry])
async def verify_object_all_copies(
    key: str, session: AsyncSession = Depends(get_session)
) -> list[FixityEntry]:
    """Fixity check across all configured targets (3.6: "regular fixity
    check across all copies") - not yet run automatically on a periodic
    basis, see Open Points in docs/services/storage-service.md."""
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
    """Fixity check basis (3.6): read the primary target's checksum fresh
    from the backend and compare it against the reference value stored in
    the shared DB. For all configured targets, see .../all."""
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
    """Retry queue for secondary copies to be asynchronously caught up
    (3.6) - deliberately an explicit endpoint instead of an in-process
    background task (see ADR 0004), intended for periodic invocation by
    an external scheduler (not yet part of this session)."""
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
    """Operational parameters (Post-Roadmap Phase 22 Session 6, ADR 0091) -
    unlike the target set itself (credentials, `Settings.targets`,
    deliberately still env-var-only), these hold no secrets and are
    therefore live-editable. The number of configured targets is
    structurally fixed (env-var, this session does not change that) -
    same quorum-satisfiability check as at startup (`_validate_settings`),
    here against an admin-chosen value instead of the env-var default."""
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
    """Correction mechanism for an intended storage device swap (3.6,
    P5c-S2, ADR 0017 follow-up item) - replaces the previously necessary
    direct correction in `backend_identity` with an API call, without a
    restart. Marks all previous copies of the target for re-replication,
    just as with an automatic degraded start (`POST
    /replication/process-pending` picks them up)."""
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
    """Admin-UI status block (3.6 "visible as status in the admin UI",
    P5b-S6): last confirmed device ID per configured target plus the
    count of not-yet-replicated copies - a target with
    `pending_copies > 0` after a degraded start is still in recovery."""
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
    """Edit target metadata live (Post-Roadmap Phase 22 Session 7,
    ADR 0092) - ONLY `object_lock_mode`/`role` per already-configured
    target ("only edit existing entries", same rule as P22-S6). `404` for
    an unknown `target_id` (the target *list* itself remains env-var-only,
    no new IDs via this endpoint). Writes the result immediately back to
    `app.state` (`_compute_target_state`), so it takes effect on every
    subsequent request without a restart."""
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
