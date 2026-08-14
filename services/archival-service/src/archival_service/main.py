import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_permission_client import PermissionServiceClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from archival_service import browse, case_pipeline, crypto, pipeline, repository
from archival_service.clients import (
    CaseClient,
    DocumentClient,
    ObjectTypeClient,
    RenderingClient,
    StorageClient,
)
from archival_service.keystore import EnvKeyStore, KeyNotFoundError
from archival_service.models import Base
from archival_service.schemas import ArchivalTransferOut, CaseArchivalTransferOut, ReleasedItemOut
from archival_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def _archival_poll_loop(session_factory) -> None:
    """Due-date poll for records disposal/dehydration (5.6) - the same
    idiom as document-service's `_retention_poll_loop`/reporting-service's
    `_report_schedule_poll_loop`: two independent phases per run, an error
    in one phase does not abort the loop."""
    while True:
        try:
            await pipeline.run_active_transfers_tick(
                session_factory,
                document_client=app.state.document_client,
                rendering_client=app.state.rendering_client,
                object_type_client=app.state.object_type_client,
                storage_client=app.state.storage_client,
                keystore=app.state.keystore,
                max_attempts=settings.max_archival_attempts,
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

        try:
            case_config = await app.state.case_client.get_archival_config()
            await case_pipeline.run_case_transfers_tick(
                session_factory,
                case_client=app.state.case_client,
                document_client=app.state.document_client,
                storage_client=app.state.storage_client,
                keystore=app.state.keystore,
                encryption_enabled=case_config.get("archive_encryption_enabled", False),
                max_attempts=settings.max_archival_attempts,
            )
        except Exception:
            logger.exception(
                "Umlaufmappen-Aussonderungs-Poll-Tick fehlgeschlagen (5.6, seit P7-S3b) - "
                "wird beim naechsten Tick erneut versucht."
            )

        await asyncio.sleep(settings.archival_poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS archival"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc migration for already-running installations (Post-Roadmap
        # Phase 20 Session 2, ADR 0078) - `create_all` creates new tables but
        # does not alter existing ones. `IF NOT EXISTS` makes this idempotent;
        # affects only additive columns with defaults (same pattern as
        # document-service's `main.py` lifespan).
        await conn.execute(
            text(
                "ALTER TABLE archival.archival_transfer "
                "ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE archival.archival_transfer "
                "ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE archival.case_archival_transfer "
                "ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0"
            )
        )
        await conn.execute(
            text(
                "ALTER TABLE archival.case_archival_transfer "
                "ADD COLUMN IF NOT EXISTS next_retry_at TIMESTAMPTZ"
            )
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.document_client = DocumentClient(settings.document_service_base_url)
    app.state.rendering_client = RenderingClient(settings.rendering_service_base_url)
    app.state.storage_client = StorageClient(settings.storage_service_base_url)
    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)
    app.state.case_client = CaseClient(settings.case_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
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
    await app.state.case_client.close()
    await app.state.permission_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


async def _require_archival_permission(x_dms_principal: str, *, access_type: str) -> None:
    """RBAC (Post-Roadmap Phase 19 Session 7, ADR 0072) - archival-service
    previously had NO general permission check at all, only the separate,
    narrower `archive_retrieval_role` gate (X-DMS-Roles) for retrieval/
    records-disposal access area/package download (5.6, "decryption only
    for authorized roles") - this gate remains UNCHANGED; the RBAC check
    here is additive, not a replacement, exactly as with case-service
    (ADR 0070). Checks `archival.read`/`archival.write` on the root
    resource (`root`) - archival-service does not register its own
    resource tree nodes. The "everyone" group (ADR 0067) grants both
    permissions by default - preserves the previous de-facto-open behavior
    while making it admin-editable."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    permission = "archival.read" if access_type == "read" else "archival.write"
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=PermissionServiceClient.ROOT_RESOURCE_ID,
        permission=permission,
        access_type=access_type,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Fehlende Berechtigung {permission!r}")


@app.get("/archival-transfers", response_model=list[ArchivalTransferOut])
async def list_archival_transfers(
    status: str | None = None,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[ArchivalTransferOut]:
    await _require_archival_permission(x_dms_principal, access_type="read")
    return await repository.list_transfers(session, status=status)


@app.get("/archival-transfers/{transfer_id}", response_model=ArchivalTransferOut)
async def get_archival_transfer(
    transfer_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ArchivalTransferOut:
    await _require_archival_permission(x_dms_principal, access_type="read")
    try:
        return await repository.get_transfer(session, transfer_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/archival-transfers/{transfer_id}/retry", response_model=ArchivalTransferOut)
async def retry_archival_transfer(
    transfer_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ArchivalTransferOut:
    """Manual restart of a permanently failed transfer (Post-Roadmap Phase
    20 Session 2, ADR 0078) - only meaningful for `failed_permanent` (409
    otherwise; a still-active or already-completed transfer doesn't need a
    manual restart)."""
    await _require_archival_permission(x_dms_principal, access_type="write")
    try:
        transfer = await repository.get_transfer(session, transfer_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if transfer.status != "failed_permanent":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Transfer hat Status {transfer.status!r}, nur 'failed_permanent' "
                "kann erneut versucht werden"
            ),
        )
    await repository.reset_for_retry(session, transfer)
    await session.commit()
    return transfer


@app.post("/archival-transfers/{transfer_id}/retrieve", response_model=ArchivalTransferOut)
async def retrieve_archival_transfer(
    transfer_id: str,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ArchivalTransferOut:
    """Retrieval (5.6, literal concept requirement: "a separate, likewise
    audited process with decryption only for authorized roles") - writes
    the decrypted archive content back to the live targets under exactly
    the same storage key as the current version, so that the regular
    `document-service` download path continues to work unchanged
    afterward."""
    await _require_archival_permission(x_dms_principal, access_type="write")
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


@app.get("/released-items", response_model=list[ReleasedItemOut])
async def list_released_items(
    q: str | None = None,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    """Records disposal access area (2.5/5.6, P15-S5) - a searchable view
    of documents/circulation folders that have already been disposed of but
    are still within the transition period, instead of making them
    findable only indirectly via audit-trail references (concept 5.6,
    literally). A pure filtered view onto the already-existing `released`
    state machine - no new data store. Same role gate as retrieval
    (`archive_retrieval_role`) - concept 2.5 names "the same roles ...
    additionally possibly a dedicated archive/registry role" for this
    special area; the already-existing retrieval role covers exactly this
    case, without introducing a second, redundant setting."""
    await _require_archival_permission(x_dms_principal, access_type="read")
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    if settings.archive_retrieval_role not in roles:
        raise HTTPException(
            status_code=403,
            detail=(
                f"Rolle {settings.archive_retrieval_role!r} erforderlich für den "
                "Aussonderungs-Zugriffsbereich"
            ),
        )
    document_transfers = await repository.list_transfers(session, status="released")
    case_transfers = await repository.list_case_transfers(session, status="released")
    return await browse.build_released_items(
        document_transfers,
        case_transfers,
        document_client=app.state.document_client,
        case_client=app.state.case_client,
        dehydration_delay_days=settings.dehydration_delay_days,
        query=q,
    )


@app.get("/case-archival-transfers", response_model=list[CaseArchivalTransferOut])
async def list_case_archival_transfers(
    status: str | None = None,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[CaseArchivalTransferOut]:
    await _require_archival_permission(x_dms_principal, access_type="read")
    return await repository.list_case_transfers(session, status=status)


@app.get("/case-archival-transfers/{transfer_id}", response_model=CaseArchivalTransferOut)
async def get_case_archival_transfer(
    transfer_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseArchivalTransferOut:
    await _require_archival_permission(x_dms_principal, access_type="read")
    try:
        return await repository.get_case_transfer(session, transfer_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/case-archival-transfers/{transfer_id}/retry", response_model=CaseArchivalTransferOut)
async def retry_case_archival_transfer(
    transfer_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> CaseArchivalTransferOut:
    """Case counterpart to `retry_archival_transfer` above (Post-Roadmap
    Phase 20 Session 2, ADR 0078)."""
    await _require_archival_permission(x_dms_principal, access_type="write")
    try:
        transfer = await repository.get_case_transfer(session, transfer_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if transfer.status != "failed_permanent":
        raise HTTPException(
            status_code=409,
            detail=(
                f"Transfer hat Status {transfer.status!r}, nur 'failed_permanent' "
                "kann erneut versucht werden"
            ),
        )
    await repository.reset_for_retry(session, transfer)
    await session.commit()
    return transfer


@app.get("/case-archival-transfers/{transfer_id}/package")
async def download_case_archival_package(
    transfer_id: str,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Delivers the XDOMEA records-disposal package (ZIP: `aussonderung.xml`
    + referenced document contents, 5.6, since P7-S3b) directly as a
    download - unlike for documents (`.../retrieve`), no writing back to a
    live target, since a circulation folder has no live storage space of
    its own."""
    await _require_archival_permission(x_dms_principal, access_type="read")
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    if settings.archive_retrieval_role not in roles:
        raise HTTPException(
            status_code=403,
            detail=f"Rolle {settings.archive_retrieval_role!r} erforderlich für die Rückholung",
        )
    try:
        transfer = await repository.get_case_transfer(session, transfer_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if transfer.status != "released":
        raise HTTPException(
            status_code=409,
            detail=f"Transfer hat Status {transfer.status!r} - kein Paket zum Herunterladen",
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

    return Response(content=data, media_type="application/zip")
