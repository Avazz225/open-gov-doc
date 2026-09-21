import hashlib
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from dms_common import MaxBodySizeMiddleware, configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_metrics_client import SensorConfigClient, bootstrap_http_sensors, metrics_payload
from dms_permission_client import PermissionServiceClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from storage_service import identity_guard, metrics, replication, repository, retention_guard
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
    BulkVerifyResult,
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
                "decommissioned": (
                    overrides[target.id].decommissioned
                    if target.id in overrides
                    else target.decommissioned
                ),
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
        # Decommissioning (Phase 40 Session 1) - same ad-hoc migration
        # pattern.
        await conn.execute(
            text(
                "ALTER TABLE storage.target_override "
                "ADD COLUMN IF NOT EXISTS decommissioned BOOLEAN NOT NULL DEFAULT FALSE"
            )
        )
        # Bulk fixity sweep (3.6 "regular fixity check", Phase 40 Session 1,
        # the shape ADR 0101's Consequences already recommended).
        await conn.execute(
            text(
                "ALTER TABLE storage.object_metadata "
                "ADD COLUMN IF NOT EXISTS next_verify_at TIMESTAMPTZ"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)

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
        sensors=metrics.sensor_declarations(),
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    await app.state.permission_client.close()
    if registration:
        await registration.stop()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

# Max upload size (Phase 61 Session 2, ADR 0187) - `upload_object`/
# `upload_archive_copy` previously read the entire request body into
# memory (`await request.body()`) with no size cap anywhere. Must be
# added here, at module level right after `app` is constructed - FastAPI
# forbids adding middleware once the app has started (same constraint the
# sensor bootstrap below already documents).
app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_upload_size_bytes)

# Sensor concept (10.1, full rollout): must run at module level, right
# after `app` is constructed - see bootstrap_http_sensors's docstring
# for why this can't move into `lifespan` (FastAPI forbids adding
# middleware once the app has started).
sensor_config_proxy, sensor_registry, _http_requests_sensor, _http_duration_sensor = (
    bootstrap_http_sensors(app, settings.service_name)
)
replication_backlog_gauge = metrics.build_sensor_registry(sensor_registry)


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


@app.get("/metrics")
async def get_metrics(session: AsyncSession = Depends(get_session)) -> Response:
    """Pull-on-scrape, not the periodic-push idiom every other sensor-
    using service uses (see `metrics.py`'s module docstring for why) -
    queries the replication backlog fresh on every call, right before
    serializing. Excluded from the generic HTTP request/duration sensors
    already (`dms_metrics_client`'s own `_EXCLUDED_PATHS`), so this
    doesn't inflate those counters."""
    if replication_backlog_gauge.is_active():
        pending_counts = await repository.count_pending_copies_by_backend(session)
        replication_backlog_gauge.set(float(sum(pending_counts.values())))
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


# Trusted internal callers of the object-CRUD data plane (Phase 59 Session 2,
# ADR 0179). storage-service is never called directly by any frontend for
# these endpoints - confirmed via grep, `apps/admin-ui`'s only calls into
# this service target the already-gated `admin.storage` config/guard
# endpoints below. Every legitimate caller is one of these six services,
# each already having checked the real end user's own permission (e.g.
# `document.read`/`.write`) before ever reaching storage-service - the same
# "fixed system-identity header" convention this project already uses
# repeatedly (`migration-service`'s `_require_workflow_service_caller`,
# `teamspace-service`'s `_require_auth_service_caller`), extended here to
# accept a SET of known callers instead of exactly one, since the object
# data plane genuinely has several legitimate machine callers, unlike those
# single-caller precedents.
_TRUSTED_STORAGE_CALLERS = frozenset(
    {
        "document-service",
        "archival-service",
        "rendering-service",
        "ocr-service",
        "virus-scan-service",
        "mail-connector",
        # P66-S1: `reporting-service` was always a real, actively-used
        # caller of `PUT`/`GET /objects/{key}` (report file storage/
        # retrieval, `StorageClient.upload()`/`.download()`) but was never
        # added here when ADR 0179 first gated the object-CRUD endpoints -
        # a real, currently-broken bug (403 on every report-file store/
        # fetch), found incidentally while adding the gate to the three
        # aggregate/maintenance endpoints below. Fixed here rather than
        # left for a dedicated session, since it's the same one-line fix.
        "reporting-service",
        # The Helm chart's storage CronJob (`storageCronJob`, ADR 0101) -
        # already sends this exact identity, anticipating this gate before
        # it existed ("even if storage-service later gates this endpoint").
        "system:storage-replication-cronjob",
    }
)


async def _require_storage_caller(x_dms_principal: str = Header(default="")) -> None:
    """Gates every object-CRUD endpoint below - these previously had NO
    permission check of any kind, reachable by any authenticated user
    through the gateway (whose own verified `X-DMS-Principal` is their own
    Keycloak `sub`, never one of the literal service-name strings checked
    here - unspoofable for a real end user, same reasoning as the
    single-caller precedents this mirrors)."""
    if x_dms_principal not in _TRUSTED_STORAGE_CALLERS:
        raise HTTPException(
            status_code=403,
            detail="Nur bekannte interne Dienste dürfen auf Objekte zugreifen",
        )


@app.get(
    "/objects/{key:path}/copies",
    response_model=list[ObjectCopyOut],
    dependencies=[Depends(_require_storage_caller)],
)
async def list_object_copies(
    key: str, session: AsyncSession = Depends(get_session)
) -> list[ObjectCopyOut]:
    return await repository.list_copies(session, key)


@app.put(
    "/objects/{key:path}/archive-copy",
    response_model=ObjectMetadataOut,
    status_code=201,
    dependencies=[Depends(_require_storage_caller)],
)
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


@app.get("/objects/{key:path}/archive-copy", dependencies=[Depends(_require_storage_caller)])
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


@app.get(
    "/objects/{key:path}/archive-copy/verify",
    response_model=list[FixityEntry],
    dependencies=[Depends(_require_storage_caller)],
)
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


@app.delete(
    "/objects/{key:path}/live-copies",
    status_code=204,
    dependencies=[Depends(_require_storage_caller)],
)
async def delete_live_copies(
    key: str,
    bypass_governance: bool = False,
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """ "Dehydrating" (5.6, since P7-S3): removes the copy/copies on the
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


@app.put(
    "/objects/{key:path}",
    response_model=ObjectMetadataOut,
    status_code=201,
    dependencies=[Depends(_require_storage_caller)],
)
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


@app.get("/objects/{key:path}", dependencies=[Depends(_require_storage_caller)])
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


@app.delete("/objects/{key:path}", status_code=204, dependencies=[Depends(_require_storage_caller)])
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


@app.get(
    "/object-metadata/{key:path}",
    response_model=ObjectMetadataOut,
    dependencies=[Depends(_require_storage_caller)],
)
async def get_object_metadata(
    key: str, session: AsyncSession = Depends(get_session)
) -> ObjectMetadataOut:
    try:
        return await repository.get_metadata(session, key)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail="Objekt nicht gefunden") from exc


@app.get(
    "/storage/usage",
    response_model=list[StorageUsageEntry],
    dependencies=[Depends(_require_storage_caller)],
)
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


@app.get(
    "/object-verify/{key:path}/all",
    response_model=list[FixityEntry],
    dependencies=[Depends(_require_storage_caller)],
)
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


@app.get(
    "/object-verify/{key:path}",
    response_model=VerifyResult,
    dependencies=[Depends(_require_storage_caller)],
)
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


@app.post(
    "/replication/process-pending",
    response_model=ReplicationRunResult,
    dependencies=[Depends(_require_storage_caller)],
)
async def replication_process_pending(
    limit: int = 100, session: AsyncSession = Depends(get_session)
) -> ReplicationRunResult:
    """Retry queue for secondary copies to be asynchronously caught up
    (3.6) - deliberately an explicit endpoint instead of an in-process
    background task (see ADR 0004), intended for periodic invocation by
    an external scheduler. **Since P66-S1**: gated via `_require_storage_
    caller` (ADR 0101 already anticipated this - the Helm chart's
    CronJob has sent `X-DMS-Principal: system:storage-replication-
    cronjob` since it was built)."""
    operational_config = await _get_operational_config(session)
    result = await replication.process_pending(
        session,
        backends=app.state.backends,
        max_attempts=operational_config.max_replication_attempts,
        limit=limit,
        lock_target_ids=app.state.lock_target_ids,
    )
    await session.commit()
    return result


@app.post(
    "/object-verify/process-pending",
    response_model=BulkVerifyResult,
    dependencies=[Depends(_require_storage_caller)],
)
async def verify_pending_objects(
    limit: int = 100, session: AsyncSession = Depends(get_session)
) -> BulkVerifyResult:
    """Bulk fixity sweep (3.6 "regular fixity check across all copies",
    Phase 40 Session 1) - the endpoint ADR 0101's Consequences section
    already recommended once a real periodic mechanism existed. Unlike
    `GET /object-verify/{key:path}/all` (verifies all TARGETS of one
    already-known object), this picks the `limit` objects verified
    longest ago (never-verified counts as most overdue) and re-verifies
    each of them - mirroring `POST /replication/process-pending`'s shape
    exactly: an explicit endpoint instead of an in-process background
    task (ADR 0004), intended for periodic invocation by an external
    scheduler. **Since P66-S1**: gated via `_require_storage_caller`,
    same as `/replication/process-pending` above - no longer deliberately
    ungated (ADR 0101's own rationale for the exception no longer holds
    now that this session closed it)."""
    result = await replication.verify_pending(
        session,
        backends=app.state.backends,
        limit=limit,
        interval_seconds=settings.fixity_verify_interval_seconds,
    )
    await session.commit()
    return result


@app.get("/operational-config", response_model=OperationalConfigOut)
async def get_operational_config(
    session: AsyncSession = Depends(get_session),
) -> OperationalConfigOut:
    return await _get_operational_config(session)


async def _require_storage_permission(x_dms_principal: str) -> None:
    """RBAC (Post-Roadmap Phase 38 Session 3) - `guard-config`/`guard-
    status/{id}/config`/`guard-status/{id}/reidentify`/`operational-config`
    previously had NO permission check at all - anyone with network access
    could, e.g., disable the degraded-start guard or reassign a target's
    `role`/`object_lock_mode`. Wires up the EXISTING capability
    `admin.storage` (role "domain-admin-storage") for the first time - it
    had been seeded in `permission-service` since P9-S1 but never actually
    enforced anywhere in the codebase until this session."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    if not await app.state.permission_client.has_permission(x_dms_principal, "admin.storage"):
        raise HTTPException(
            status_code=403,
            detail="Fehlende Domain-Admin-Rolle 'Storage-/Backend-Verwaltung'",
        )


@app.put("/operational-config", response_model=OperationalConfigOut)
async def put_operational_config(
    body: OperationalConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> OperationalConfigOut:
    """Operational parameters (Post-Roadmap Phase 22 Session 6, ADR 0091) -
    unlike the target set itself (credentials, `Settings.targets`,
    deliberately still env-var-only), these hold no secrets and are
    therefore live-editable. The number of configured targets is
    structurally fixed (env-var, this session does not change that) -
    same quorum-satisfiability check as at startup (`_validate_settings`),
    here against an admin-chosen value instead of the env-var default.
    Gated by `admin.storage` since Post-Roadmap Phase 38 Session 3, see
    `_require_storage_permission`."""
    await _require_storage_permission(x_dms_principal)
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
    body: GuardConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> GuardConfigOut:
    await _require_storage_permission(x_dms_principal)
    config = await repository.update_guard_config(
        session, allow_degraded_start=body.allow_degraded_start
    )
    await session.commit()
    return config


@app.post("/guard-status/{target_id}/reidentify", response_model=GuardStatusEntry)
async def reidentify_target(
    target_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> GuardStatusEntry:
    """Correction mechanism for an intended storage device swap (3.6,
    P5c-S2, ADR 0017 follow-up item) - replaces the previously necessary
    direct correction in `backend_identity` with an API call, without a
    restart. Marks all previous copies of the target for re-replication,
    just as with an automatic degraded start (`POST
    /replication/process-pending` picks them up). Gated by `admin.storage`
    since Post-Roadmap Phase 38 Session 3, see `_require_storage_permission`."""
    await _require_storage_permission(x_dms_principal)
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
        decommissioned=configs[target_id].decommissioned if target_id in configs else False,
    )


@app.get("/guard-status", response_model=list[GuardStatusEntry])
async def get_guard_status(session: AsyncSession = Depends(get_session)) -> list[GuardStatusEntry]:
    """Admin-UI status block (3.6 "visible as status in the admin UI",
    P5b-S6): last confirmed device ID per configured target plus the
    count of not-yet-replicated copies - a target with
    `pending_copies > 0` after a degraded start is still in recovery.
    Iterates `app.state.target_configs` (every configured target,
    unfiltered), NOT `app.state.targets` (Phase 40 Session 1 - the latter
    already excluded `role="archive"` targets from this status view
    before this session, and would now do the same for a decommissioned
    one, making it impossible to discover and un-decommission a target
    through this endpoint/the Admin UI)."""
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
            decommissioned=configs[target_id].decommissioned if target_id in configs else False,
        )
        for target_id in configs
    ]


@app.put("/guard-status/{target_id}/config", response_model=GuardStatusEntry)
async def put_target_config(
    target_id: str,
    body: TargetConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> GuardStatusEntry:
    """Edit target metadata live (Post-Roadmap Phase 22 Session 7,
    ADR 0092) - ONLY `object_lock_mode`/`role`/`decommissioned` per
    already-configured target ("only edit existing entries", same rule as
    P22-S6). `404` for an unknown `target_id` (the target *list* itself
    remains env-var-only, no new IDs via this endpoint). Writes the
    result immediately back to `app.state` (`_compute_target_state`), so
    it takes effect on every subsequent request without a restart. Gated
    by `admin.storage` since Post-Roadmap Phase 38 Session 3, see
    `_require_storage_permission`.

    `decommissioned` (Phase 40 Session 1): excludes the target from
    `resolve_targets()`/`resolve_archive_targets()` - `422` under the same
    three conditions a role toggle already had to guard against, now
    shared by both: (a) no regular target would remain, (b) the
    already-configured `quorum_count` (`PUT /operational-config`) would
    become unsatisfiable against the smaller target count - previously
    UNCHECKED for a role toggle too, a real, documented gap (see
    `docs/services/storage-service.md` Open Points) - or (c), specific to
    decommissioning, some object's only confirmed (`ok`) copy lives
    exclusively on this target (`count_sole_ok_copies_for_backend`) -
    removing it would make that object's data permanently unreachable,
    not merely orphan a row. Once these checks pass, every existing
    `object_copy` row for this target is deleted
    (`remove_copies_for_backend`) - the exact cleanup step missing before
    this session, which is what produced 30,410 permanently orphaned rows
    in a real incident. Un-decommissioning (`decommissioned: false` on a
    previously decommissioned target) re-seeds `pending` copies for it
    (`seed_pending_copies_for_new_target`), the same rebalancing a newly
    added target already gets (P5c-S2) - symmetrical treatment of
    "target re-enters service"."""
    await _require_storage_permission(x_dms_principal)
    if target_id not in {t.id for t in settings.targets}:
        raise HTTPException(status_code=404, detail=f"Unbekanntes Ziel: {target_id!r}")

    existing_overrides = {o.target_id: o for o in await repository.list_target_overrides(session)}
    was_decommissioned = (
        existing_overrides[target_id].decommissioned if target_id in existing_overrides else False
    )
    would_be_overrides = dict(existing_overrides)
    would_be_overrides[target_id] = TargetOverride(
        target_id=target_id,
        object_lock_mode=body.object_lock_mode,
        role=body.role,
        decommissioned=body.decommissioned,
    )
    _, would_be_targets, _, _ = _compute_target_state(settings.targets, would_be_overrides)
    if not would_be_targets:
        raise HTTPException(
            status_code=422,
            detail=(
                f"role={body.role!r}/decommissioned={body.decommissioned!r} für {target_id!r} "
                "würde kein reguläres Ziel mehr übrig lassen (mindestens eines muss außerhalb "
                "role=archive/decommissioned=true bleiben)"
            ),
        )

    operational_config = await _get_operational_config(session)
    if operational_config.write_strategy == "quorum" and not (
        1 <= operational_config.quorum_count <= len(would_be_targets)
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Änderung für {target_id!r} würde quorum_count={operational_config.quorum_count} "
                f"mit nur {len(would_be_targets)} verbleibenden regulären Ziel(en) nicht mehr "
                "erfüllbar machen (siehe PUT /operational-config)"
            ),
        )

    if body.decommissioned and not was_decommissioned:
        sole_copies = await repository.count_sole_ok_copies_for_backend(session, target_id)
        if sole_copies:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Ziel {target_id!r} kann nicht dekommissioniert werden: {sole_copies} "
                    "Objekt(e) haben ausschließlich auf diesem Ziel eine bestätigte Kopie - "
                    "zuerst auf ein anderes Ziel replizieren"
                ),
            )

    await repository.upsert_target_override(
        session,
        target_id,
        object_lock_mode=body.object_lock_mode,
        role=body.role,
        decommissioned=body.decommissioned,
    )

    if body.decommissioned and not was_decommissioned:
        removed = await repository.remove_copies_for_backend(session, target_id)
        logger.warning(
            "Ziel %r dekommissioniert: %s object_copy-Zeile(n) entfernt", target_id, removed
        )
    elif was_decommissioned and not body.decommissioned:
        seeded = await repository.seed_pending_copies_for_new_target(session, target_id)
        logger.info(
            "Ziel %r reaktiviert: %s Objekt(e) zur Nachreplikation vorgemerkt", target_id, seeded
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
        decommissioned=configs[target_id].decommissioned if target_id in configs else False,
    )
