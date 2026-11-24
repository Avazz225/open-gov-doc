import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from cryptography import x509
from dms_common import ActingIdentity, configure_logging, require_acting_identity
from dms_common.otel import setup_observability
from dms_common.shutdown import bounded_shutdown_step
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient, SubjectNotFoundError
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_permission_client import PermissionServiceClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, File, Header, HTTPException, Response, UploadFile, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from signature_service import incoming_repository, repository
from signature_service.auth_client import AuthServiceClient
from signature_service.connectors import build_connectors, resolve_connector_for_level
from signature_service.document_client import DocumentServiceClient, LockConflictError
from signature_service.document_client import NotFoundError as DocumentNotFoundError
from signature_service.incoming import IncomingSignatureService
from signature_service.models import Base
from signature_service.object_type_client import NotFoundError as ObjectTypeNotFoundError
from signature_service.object_type_client import ObjectTypeServiceClient
from signature_service.schemas import (
    BackfillIn,
    BackfillStatusOut,
    IncomingSignaturesOut,
    IncomingSignatureSummaryOut,
    IncomingSignatureValidationOut,
    IncomingTrustAnchorIn,
    IncomingTrustAnchorOut,
    IncomingValidationConfigIn,
    IncomingValidationConfigOut,
    RevalidateIn,
    RevalidateOut,
    SignatureCreate,
    SignatureOut,
    SignatureProviderLevelsIn,
    SignatureProviderStatusOut,
    TrustedListOut,
    TrustedListsOut,
    VerificationOut,
)
from signature_service.settings import Settings, SignatureProviderConfig
from signature_service.storage_client import StorageClient
from signature_service.validation.trust import (
    InvalidAnchorError,
    der_of,
    fingerprint_sha256,
    parse_anchor_pem,
)
from signature_service.validation.trusted_lists import TrustedListError

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_LEVEL_RANK = {"ses": 0, "aes": 1, "qes": 2}


async def _run_retimestamp_tick(app: FastAPI, *, cutoff: datetime) -> None:
    """One pass of the PAdES-B-LTA periodic archive-timestamp-chain
    extension (3.10, Post-Roadmap Phase 41 Session 1) - self-contained
    within this service (reuses the already-existing `document_client`
    for fetch/checkin), no new cross-service dependency on
    `archival-service` needed (see docs/services/archival-service.md).
    Per-item try/except, same fault-tolerant idiom as every other
    multi-phase poll loop in this project (e.g. `archival_service.main.
    _archival_poll_loop`) - one signature's failure must not abort the
    whole batch. `cutoff` is computed by the caller (not read from
    `Settings` in here) so tests can force a tick to find something "due"
    without mutating the module-level `settings` singleton - every
    `TestClient`-started app instance runs its own real
    `_retimestamp_poll_loop` in the background, and mutating shared
    global state would race with those instances' own ticks."""
    async with app.state.session_factory() as session:
        due = await repository.list_signatures_due_for_retimestamp(session, cutoff=cutoff)
        for signature in due:
            try:
                connector = app.state.connectors.get(signature.connector_id)
                extend = getattr(connector, "extend_timestamp_chain", None)
                if extend is None:
                    continue
                _content_type, pdf_bytes = await app.state.document_client.get_version_content(
                    signature.document_id, signature.version_number
                )
                extended_bytes = await extend(pdf_bytes)
                checkin = await app.state.document_client.checkin_signed_version(
                    signature.document_id,
                    expected_base_version_number=signature.version_number,
                    signed_bytes=extended_bytes,
                    filename=f"retimestamped-{signature.document_id}-v{signature.version_number}.pdf",
                    created_by="signature-service",
                    comment="Archiv-Zeitstempel erneuert (PAdES-B-LTA, 3.10)",
                )
                await repository.mark_timestamped(
                    session,
                    signature.id,
                    version_number=checkin["version"]["version_number"],
                )
                await session.commit()
            except Exception:
                logger.exception(
                    "Archiv-Zeitstempel-Erneuerung für signature_id=%s fehlgeschlagen",
                    signature.id,
                )
                await session.rollback()


async def _trusted_list_poll_loop(app: FastAPI) -> None:
    """Scheduled fetch of the EU trusted lists (P92-S2): soon after start if
    none are stored yet, then every `trusted_list_poll_interval_seconds` -
    only while the source is switched on."""
    interval = settings.trusted_list_poll_interval_seconds
    async with app.state.session_factory() as session:
        have_lists = await incoming_repository.get_lotl(session) is not None
    await asyncio.sleep(60 if not have_lists else interval)
    while True:
        try:
            async with app.state.session_factory() as session:
                config = await incoming_repository.get_config(session)
                enabled = config.eu_trusted_lists
                await session.commit()
            if enabled:
                await app.state.incoming.refresh_trusted_lists(triggered_by="system:schedule")
        except Exception:
            logger.exception("trusted_list_poll_failed")
        await asyncio.sleep(interval)


async def _retimestamp_poll_loop(app: FastAPI) -> None:
    while True:
        try:
            cutoff = datetime.now(UTC) - timedelta(days=settings.retimestamp_interval_days)
            await _run_retimestamp_tick(app, cutoff=cutoff)
        except Exception:
            logger.exception("Archiv-Zeitstempel-Poll-Tick fehlgeschlagen")
        await asyncio.sleep(settings.retimestamp_poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS signature"))
        await conn.run_sync(Base.metadata.create_all)
        # PAdES-B-LTA (3.10, Post-Roadmap Phase 41 Session 1) - ad-hoc
        # migration like everywhere in this system (no Alembic).
        await conn.execute(
            text(
                "ALTER TABLE signature.signature "
                "ADD COLUMN IF NOT EXISTS last_timestamped_at TIMESTAMPTZ"
            )
        )
        # P92-S2 additions to the tables from P92-S1.
        for statement in (
            "ALTER TABLE signature.incoming_validation_config "
            "ADD COLUMN IF NOT EXISTS eu_trusted_lists BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE signature.incoming_signature_validation "
            "ADD COLUMN IF NOT EXISTS qc_type VARCHAR(8)",
            "ALTER TABLE signature.incoming_signature_validation "
            "ADD COLUMN IF NOT EXISTS report_key VARCHAR(512)",
        ):
            await conn.execute(text(statement))
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    async with app.state.session_factory() as session:
        ca = await repository.get_or_create_ca(session)
        tsa = await repository.get_or_create_tsa(
            session,
            ca_certificate_pem=ca.certificate_pem,
            ca_private_key_pem=ca.private_key_pem,
        )
        await session.commit()
        ca_certificate_pem, ca_private_key_pem = ca.certificate_pem, ca.private_key_pem
        tsa_certificate_pem, tsa_private_key_pem = tsa.certificate_pem, tsa.private_key_pem

    app.state.connectors = build_connectors(
        settings,
        ca_certificate_pem=ca_certificate_pem,
        ca_private_key_pem=ca_private_key_pem,
        tsa_certificate_pem=tsa_certificate_pem,
        tsa_private_key_pem=tsa_private_key_pem,
    )
    app.state.document_client = DocumentServiceClient(settings.document_service_base_url)
    app.state.object_type_client = ObjectTypeServiceClient(settings.object_type_service_base_url)
    app.state.auth_client = AuthServiceClient(settings.auth_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.storage = StorageClient(settings.storage_service_base_url)

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    producer = NatsEventBusClient(settings.nats_url, stream="signature")
    await producer.connect()
    app.state.producer = producer

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        sensors=http_sensor_declarations(),
    )

    app.state.retimestamp_task = asyncio.create_task(_retimestamp_poll_loop(app))

    # Incoming-signature validation (P92-S1, ADR 0248): a durable consumer on
    # `document.>` with `deliver_new` - the existing estate isn't replayed
    # (a backfill endpoint follows in P92-S2) - feeding a bounded worker.
    app.state.internal_ca_pem = ca_certificate_pem
    incoming = IncomingSignatureService(
        settings=settings,
        session_factory=app.state.session_factory,
        document_client=app.state.document_client,
        internal_ca_pem=ca_certificate_pem,
        publish_event=publish_event,
        storage=app.state.storage,
    )
    incoming.worker.start()
    app.state.incoming = incoming
    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    try:
        await consumer_bus.subscribe(
            "document.>",
            incoming.handle_event,
            durable="signature-service-incoming",
            deliver_new=True,
        )
    except SubjectNotFoundError:
        logger.warning("incoming_signature_stream_missing")
    app.state.trusted_list_task = (
        asyncio.create_task(_trusted_list_poll_loop(app))
        if settings.trusted_list_poll_interval_seconds > 0
        else None
    )
    app.state.background_tasks = set()

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    # Every step bounded and named (dms_common.shutdown).
    app.state.retimestamp_task.cancel()
    await bounded_shutdown_step("retimestamp_task", app.state.retimestamp_task)
    if app.state.trusted_list_task is not None:
        app.state.trusted_list_task.cancel()
        await bounded_shutdown_step("trusted_list_task", app.state.trusted_list_task)
    for task in list(app.state.background_tasks):
        task.cancel()
    await bounded_shutdown_step("incoming.stop_backfill", incoming.stop_backfill())
    await bounded_shutdown_step("consumer_bus.close", consumer_bus.close())
    await bounded_shutdown_step("incoming.worker.stop", incoming.worker.stop())
    sensor_config_proxy.unbind()
    await bounded_shutdown_step("sensor_config_client.stop", app.state.sensor_config_client.stop())
    if registration:
        await bounded_shutdown_step("registration.stop", registration.stop())
    await bounded_shutdown_step("producer.close", producer.close())
    for name in (
        "document_client",
        "object_type_client",
        "auth_client",
        "permission_client",
        "storage",
    ):
        await bounded_shutdown_step(f"{name}.close", getattr(app.state, name).close())
    await bounded_shutdown_step("engine.dispose", engine.dispose())


app = FastAPI(title=settings.service_name, lifespan=lifespan)
setup_observability(app, settings)

# Sensor concept (10.1, full rollout): must run at module level, right
# after `app` is constructed - see bootstrap_http_sensors's docstring
# for why this can't move into `lifespan` (FastAPI forbids adding
# middleware once the app has started).
sensor_config_proxy, sensor_registry, _http_requests_sensor, _http_duration_sensor = (
    bootstrap_http_sensors(app, settings.service_name)
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def _reject_during_maintenance(x_dms_maintenance_active: str) -> None:
    """Maintenance mode (4.8), Category A request-triggered cascading writes
    (Phase 56 Session 1, ADR 0152) - same helper shape and message as
    `workflow-service`'s own `_reject_during_maintenance` (P6-S6), reading
    the header the gateway already forwards rather than an extra
    `permission-service` round trip. Guards `create_signature` specifically -
    the one endpoint here that cascades into another service's write path
    (`document_client.checkin_signed_version`, a real new document version
    in `document-service`)."""
    if x_dms_maintenance_active.lower() == "true":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Systemweite Notfallsperre aktiv - Wartungsmodus",
        )


def _default_provider_levels() -> dict[str, list[str]]:
    return {config.id: list(config.levels) for config in settings.signature_providers}


async def _effective_providers(session: AsyncSession) -> list[SignatureProviderConfig]:
    """Read fresh from the DB on every call (no `app.state` cache) -
    post-roadmap phase 22 session 6, ADR 0091: makes `levels` take effect
    without a restart. `id`/`type` still come structurally from `Settings`,
    only `levels` can be overridden by the `SignatureConfig` row."""
    config = await repository.get_signature_config(
        session, default_provider_levels=_default_provider_levels()
    )
    return [
        SignatureProviderConfig(
            id=provider.id,
            type=provider.type,
            levels=config.provider_levels.get(provider.id, provider.levels),
        )
        for provider in settings.signature_providers
    ]


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
    await app.state.producer.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


async def _require_document_write_permission(x_dms_principal: str, document_id: str) -> None:
    """RBAC (Phase 59 Session 3) - checks the CALLER's own `document.write`
    against the target document's resource tree, directly via
    `permission-service` - `document_client.py`'s own calls into
    `document-service` always assert the fixed, elevated `X-DMS-Principal:
    signature-service` identity (covered by "everyone"'s baseline grants),
    so without this check document-service never learns who the real
    caller actually was, and a caller with no access to the target
    document at all could still sign (and check in a new version of) it.
    Mirrors `document_service.main._require_document_permission`'s own
    shape/resource_id convention (ADR 0154: a document's own `id` is its
    `resource_id`), via the generic `check()` on the shared
    `dms_permission_client` (this service doesn't have a document-specific
    convenience wrapper the way `document-service` does its own,
    duplicated client). Callers resolve `404` (unknown document) themselves
    first, same existence-before-permission ordering convention as
    `document_service.main._require_document_permission`'s own callers."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=document_id,
        permission="document.write",
        access_type="write",
    )
    if not allowed:
        raise HTTPException(
            status_code=403, detail=f"Fehlende Berechtigung 'document.write' auf {document_id!r}"
        )


async def _require_document_read_permission(x_dms_principal: str, document_id: str) -> None:
    """Read counterpart of `_require_document_write_permission` above, for
    the three `GET` endpoints below (Phase 59 Session 3) - same
    previously-missing-entirely gap, same fix shape."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=document_id,
        permission="document.read",
        access_type="read",
    )
    if not allowed:
        raise HTTPException(
            status_code=403, detail=f"Fehlende Berechtigung 'document.read' auf {document_id!r}"
        )


@app.post("/signatures", response_model=SignatureOut, status_code=status.HTTP_201_CREATED)
async def create_signature(
    payload: SignatureCreate,
    session: AsyncSession = Depends(get_session),
    identity: ActingIdentity = Depends(require_acting_identity),
    x_dms_principal: str = Header(default=""),
    x_dms_username: str = Header(default=""),
    x_dms_maintenance_active: str = Header(default="false"),
) -> SignatureOut:
    await _reject_during_maintenance(x_dms_maintenance_active)
    # P85-S3 (ADR 0240): a signature is never made on someone's behalf - a
    # service may act for a user elsewhere, but not here. (No principal at
    # all is already a 401 from `require_acting_identity`.) The existing
    # check below binds the signer to the verified username.
    if identity.on_behalf_of:
        raise HTTPException(
            status_code=403, detail="Signieren im Auftrag einer anderen Person ist nicht möglich"
        )
    try:
        document = await app.state.document_client.get_document(payload.document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_document_write_permission(x_dms_principal, payload.document_id)
    if payload.signer_principal_id != x_dms_username:
        raise HTTPException(
            status_code=403,
            detail=(
                "signer_principal_id muss mit der eigenen, verifizierten Identität "
                "(X-DMS-Username) übereinstimmen - Signieren im Namen einer anderen "
                "Person ist nicht möglich"
            ),
        )

    source_version_number = payload.version_number or document["current_version_number"]
    try:
        content_type, pdf_bytes = await app.state.document_client.get_version_content(
            payload.document_id, source_version_number
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    if content_type != "application/pdf":
        raise HTTPException(
            status_code=400,
            detail=(
                f"Nur PDF-Dokumente können signiert werden (PAdES), "
                f"gefundener content_type: {content_type!r}"
            ),
        )

    object_type_id = document.get("object_type_id")
    if object_type_id is not None:
        try:
            required_level = await app.state.object_type_client.get_required_signature_level(
                object_type_id
            )
        except ObjectTypeNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if required_level is not None and _LEVEL_RANK[payload.level] < _LEVEL_RANK[required_level]:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Objekttyp verlangt mindestens Signaturniveau {required_level!r}, "
                    f"angefordert wurde {payload.level!r}"
                ),
            )

    signer = await app.state.auth_client.resolve_signer(payload.signer_principal_id)
    if signer is None:
        raise HTTPException(
            status_code=400, detail=f"Unbekannter Principal {payload.signer_principal_id!r}"
        )

    effective_providers = await _effective_providers(session)
    resolved = resolve_connector_for_level(effective_providers, app.state.connectors, payload.level)
    if resolved is None:
        raise HTTPException(
            status_code=400,
            detail=f"Kein Signature-Provider-Connector für Niveau {payload.level!r} konfiguriert",
        )
    connector_id, connector = resolved

    signed = await connector.sign(pdf_bytes, signer=signer, level=payload.level)

    try:
        checkin = await app.state.document_client.checkin_signed_version(
            payload.document_id,
            expected_base_version_number=source_version_number,
            signed_bytes=signed.signed_pdf_bytes,
            filename=f"signed-{payload.document_id}-v{source_version_number}.pdf",
            # The signer's principal, not a display name (P85-S4, ADR 0240).
            created_by=identity.actor,
            comment=f"Elektronische Signatur ({payload.level.upper()})",
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LockConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    signature = await repository.create_signature(
        session,
        document_id=payload.document_id,
        source_version_number=source_version_number,
        version_number=checkin["version"]["version_number"],
        level=payload.level,
        connector_id=connector_id,
        signer_principal_id=payload.signer_principal_id,
        signer_display_name=signer.display_name,
        certificate_subject=signed.certificate_subject,
        certificate_serial=signed.certificate_serial,
        certificate_not_before=signed.certificate_not_before,
        certificate_not_after=signed.certificate_not_after,
        reason=payload.reason,
    )
    await session.commit()

    await publish_event(
        "signature.created",
        subject=payload.document_id,
        payload={
            "version_number": signature.version_number,
            "level": signature.level,
            "signer_principal_id": signature.signer_principal_id,
            "connector_id": connector_id,
        },
        actor=signature.signer_principal_id,
    )
    return signature


@app.get("/signature-config", response_model=list[SignatureProviderStatusOut])
async def get_signature_config(
    session: AsyncSession = Depends(get_session),
) -> list[SignatureProviderStatusOut]:
    providers = await _effective_providers(session)
    return [SignatureProviderStatusOut(id=p.id, type=p.type, levels=p.levels) for p in providers]


async def _require_signature_config_permission(x_dms_principal: str) -> None:
    """RBAC (Post-Roadmap Phase 38 Session 3) - `PUT /signature-config`
    previously had NO permission check at all. New capability
    `admin.signature_config` (role "domain-admin-signature") - a dedicated
    domain rather than reusing `admin.object_config`/`admin.storage`,
    since electronic-signature provider configuration (3.10) is a
    materially different, more specialized concern (which levels an
    installation's connectors may issue) than either object-type schema
    or storage-backend administration."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    if not await app.state.permission_client.has_permission(
        x_dms_principal, "admin.signature_config"
    ):
        raise HTTPException(
            status_code=403,
            detail="Fehlende Domain-Admin-Rolle 'Signatur-Konfiguration'",
        )


@app.put("/signature-config", response_model=list[SignatureProviderStatusOut])
async def put_signature_config(
    body: list[SignatureProviderLevelsIn],
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[SignatureProviderStatusOut]:
    """Connector levels (post-roadmap phase 22 session 6, ADR 0091) - `id`/
    `type` still come structurally from `Settings.signature_providers`
    ("only edit existing entries"), only `levels` is editable. Same
    validation as `SignatureProviderConfig._check_levels` (settings
    schema), here at runtime instead of at startup. Gated by
    `admin.signature_config` since Post-Roadmap Phase 38 Session 3, see
    `_require_signature_config_permission`."""
    await _require_signature_config_permission(x_dms_principal)
    known_provider_types = {p.id: p.type for p in settings.signature_providers}
    try:
        await repository.update_signature_config(
            session,
            provider_levels={item.id: item.levels for item in body},
            known_provider_types=known_provider_types,
            default_provider_levels=_default_provider_levels(),
        )
    except repository.InvalidProviderLevelsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await session.commit()
    providers = await _effective_providers(session)
    return [SignatureProviderStatusOut(id=p.id, type=p.type, levels=p.levels) for p in providers]


@app.get("/signatures", response_model=list[SignatureOut])
async def list_signatures(
    document_id: str | None = None,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[SignatureOut]:
    """`document_id` is required (Phase 59 Session 3) - previously
    optional, and an omitted filter returned every signature in the
    system regardless of caller. The one real frontend caller
    (`user-ui`'s `SignaturesPanel`) always already passes it."""
    if not document_id:
        raise HTTPException(
            status_code=400, detail="document_id ist erforderlich (kein systemweites Listing)"
        )
    await _require_document_read_permission(x_dms_principal, document_id)
    return await repository.list_signatures(session, document_id=document_id)


@app.get("/signatures/due-for-retimestamp", response_model=list[SignatureOut])
async def list_signatures_due_for_retimestamp(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[SignatureOut]:
    """P71-S4 (3.10, ADR 0155): admin-UI visibility for the PAdES-B-LTA
    poll loop's own due-set - the exact same `cutoff`/query the poll loop
    itself uses (`repository.list_signatures_due_for_retimestamp`,
    `main.py._retimestamp_poll_loop`), so this view can never drift from
    what the loop will actually pick up on its next tick. Deliberately a
    system-wide listing (unlike `GET /signatures`, which requires
    `document_id` for per-document RBAC scoping since Phase 59 Session 3)
    - gated behind `admin.signature_config` instead, the same capability
    already gating `PUT /signature-config`, since this is an
    administrative overview of the installation's signature estate as a
    whole, not a per-document read. Deliberately READ-ONLY: ADR 0155 itself
    already explicitly decided against a manual trigger for this poll loop
    (citing `document-service`'s retention poll loop as the established
    "no manual trigger" precedent) - this session found no new
    justification to reopen that decision, so none is added here either."""
    await _require_signature_config_permission(x_dms_principal)
    cutoff = datetime.now(UTC) - timedelta(days=settings.retimestamp_interval_days)
    return await repository.list_signatures_due_for_retimestamp(session, cutoff=cutoff)


@app.get("/signatures/{signature_id}", response_model=SignatureOut)
async def get_signature(
    signature_id: int,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> SignatureOut:
    try:
        signature = await repository.get_signature(session, signature_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_document_read_permission(x_dms_principal, signature.document_id)
    return signature


@app.get("/signatures/{signature_id}/verify", response_model=VerificationOut)
async def verify_signature(
    signature_id: int,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> VerificationOut:
    try:
        signature = await repository.get_signature(session, signature_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_document_read_permission(x_dms_principal, signature.document_id)

    try:
        _content_type, pdf_bytes = await app.state.document_client.get_version_content(
            signature.document_id, signature.version_number
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    connector = app.state.connectors[signature.connector_id]
    result = await connector.verify(pdf_bytes)

    certificate_expired = datetime.now(UTC) > signature.certificate_not_after
    errors = list(result.errors)
    if certificate_expired:
        errors.append("Zertifikat ist mittlerweile abgelaufen")

    return VerificationOut(
        valid=result.integrity_intact and result.trusted and not certificate_expired,
        integrity_intact=result.integrity_intact,
        certificate_expired=certificate_expired,
        errors=errors,
    )


# --- Incoming-signature validation (P92-S1, ADR 0248) ----------------------


@app.get("/incoming-signatures", response_model=IncomingSignaturesOut)
async def get_incoming_signatures(
    document_id: str,
    version_number: int | None = None,
    history: bool = False,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> IncomingSignaturesOut:
    """Validation results for a document's versions: the per-version summary
    and the rows of the latest run (`history=true`: every run). Needs
    `document.read` on the document."""
    await _require_document_read_permission(x_dms_principal, document_id)
    summaries = await incoming_repository.list_summaries(session, document_id)
    if version_number is not None:
        summaries = [s for s in summaries if s.version_number == version_number]
    validations = await incoming_repository.list_validations(
        session, document_id, version_number=version_number, latest_only=not history
    )
    return IncomingSignaturesOut(
        document_id=document_id,
        summaries=[IncomingSignatureSummaryOut.model_validate(s) for s in summaries],
        validations=[
            IncomingSignatureValidationOut.model_validate(v).model_copy(
                update={"report_available": v.report_key is not None}
            )
            for v in validations
        ],
    )


@app.get(
    "/incoming-signatures/summaries", response_model=dict[str, list[IncomingSignatureSummaryOut]]
)
async def get_incoming_signature_summaries(
    document_ids: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> dict[str, list[IncomingSignatureSummaryOut]]:
    """Per-version summaries for several documents at once (comma-separated
    `document_ids`, at most 200) - for the badges in document lists (P92-S3).
    Documents the caller may not read are left out silently, like in the
    listings themselves (ADR 0259); documents without results are absent."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    ids = list(dict.fromkeys(i for i in document_ids.split(",") if i))
    if len(ids) > 200:
        raise HTTPException(status_code=422, detail="Höchstens 200 Dokumente je Abfrage")
    if not ids:
        return {}
    summaries = await incoming_repository.list_summaries_for(session, ids)
    if not summaries:
        return {}
    allowed = await app.state.permission_client.check_batch(
        principal_id=x_dms_principal,
        resource_ids=sorted({s.document_id for s in summaries}),
        permission="document.read",
        access_type="read",
    )
    result: dict[str, list[IncomingSignatureSummaryOut]] = {}
    for summary in summaries:
        if allowed.get(summary.document_id, False):
            result.setdefault(summary.document_id, []).append(
                IncomingSignatureSummaryOut.model_validate(summary)
            )
    return result


@app.get("/incoming-signatures/config", response_model=IncomingValidationConfigOut)
async def get_incoming_validation_config(
    session: AsyncSession = Depends(get_session),
) -> IncomingValidationConfigOut:
    config = await incoming_repository.get_config(session)
    await session.commit()
    return IncomingValidationConfigOut.model_validate(config)


@app.put("/incoming-signatures/config", response_model=IncomingValidationConfigOut)
async def put_incoming_validation_config(
    body: IncomingValidationConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> IncomingValidationConfigOut:
    await _require_signature_config_permission(x_dms_principal)
    config = await incoming_repository.update_config(
        session,
        enabled=body.enabled,
        revocation_mode=body.revocation_mode,
        outbound_fetching=body.outbound_fetching,
        eu_trusted_lists=body.eu_trusted_lists,
        updated_by=x_dms_principal,
    )
    await session.commit()
    return IncomingValidationConfigOut.model_validate(config)


def _internal_anchor_out() -> IncomingTrustAnchorOut:
    cert = x509.load_pem_x509_certificate(app.state.internal_ca_pem)
    return IncomingTrustAnchorOut(
        id=None,
        source="internal",
        label="Interne Signatur-CA (OG Doc)",
        subject=cert.subject.rfc4514_string(),
        fingerprint_sha256=fingerprint_sha256(der_of(cert)),
        not_after=cert.not_valid_after_utc,
    )


@app.get("/incoming-signatures/trust-anchors", response_model=list[IncomingTrustAnchorOut])
async def list_incoming_trust_anchors(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[IncomingTrustAnchorOut]:
    await _require_signature_config_permission(x_dms_principal)
    anchors = await incoming_repository.list_anchors(session)
    return [_internal_anchor_out()] + [
        IncomingTrustAnchorOut(
            id=a.id,
            source="installation",
            label=a.label,
            subject=a.subject,
            fingerprint_sha256=a.fingerprint_sha256,
            not_after=a.not_after,
            created_at=a.created_at,
            created_by=a.created_by,
        )
        for a in anchors
    ]


@app.post(
    "/incoming-signatures/trust-anchors",
    response_model=IncomingTrustAnchorOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_incoming_trust_anchor(
    body: IncomingTrustAnchorIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> IncomingTrustAnchorOut:
    """Uploads a CA certificate (PEM) as an installation anchor. Takes effect
    for validations from now on; earlier results stay as they were."""
    await _require_signature_config_permission(x_dms_principal)
    try:
        cert = parse_anchor_pem(body.certificate_pem.encode())
    except InvalidAnchorError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    der = der_of(cert)
    try:
        anchor = await incoming_repository.add_anchor(
            session,
            label=body.label,
            certificate_der=der,
            fingerprint_sha256=fingerprint_sha256(der),
            subject=cert.subject.rfc4514_string(),
            not_after=cert.not_valid_after_utc,
            created_by=x_dms_principal,
        )
    except incoming_repository.DuplicateAnchorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "signature.trust_anchor.added",
        subject=str(anchor.id),
        payload={"label": anchor.label, "fingerprint_sha256": anchor.fingerprint_sha256},
        actor=x_dms_principal,
    )
    return IncomingTrustAnchorOut(
        id=anchor.id,
        source="installation",
        label=anchor.label,
        subject=anchor.subject,
        fingerprint_sha256=anchor.fingerprint_sha256,
        not_after=anchor.not_after,
        created_at=anchor.created_at,
        created_by=anchor.created_by,
    )


@app.delete(
    "/incoming-signatures/trust-anchors/{anchor_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_incoming_trust_anchor(
    anchor_id: int,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _require_signature_config_permission(x_dms_principal)
    try:
        anchor = await incoming_repository.delete_anchor(session, anchor_id)
    except incoming_repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "signature.trust_anchor.removed",
        subject=str(anchor_id),
        payload={"label": anchor.label, "fingerprint_sha256": anchor.fingerprint_sha256},
        actor=x_dms_principal,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- P92-S2: re-validation, backfill, reports, EU trusted lists -------------


@app.post(
    "/incoming-signatures/revalidate",
    response_model=RevalidateOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def revalidate_incoming_signatures(
    body: RevalidateIn,
    x_dms_principal: str = Header(default=""),
) -> RevalidateOut:
    """Queues a version (default: the current one) for another run. Anyone who
    may read the document may ask; the result is added, earlier runs stay."""
    try:
        await app.state.document_client.get_document(body.document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_document_read_permission(x_dms_principal, body.document_id)
    try:
        version = await app.state.incoming.revalidate(
            body.document_id, body.version_number, requested_by=x_dms_principal
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RevalidateOut(document_id=body.document_id, version_number=version)


@app.post(
    "/incoming-signatures/backfill",
    response_model=BackfillStatusOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_incoming_signature_backfill(
    body: BackfillIn,
    x_dms_principal: str = Header(default=""),
) -> BackfillStatusOut:
    """Validates existing PDF versions - of one folder's documents (not its
    subfolders) and/or created in a time range. Runs in the background;
    `GET /incoming-signatures/backfill` shows progress. One at a time."""
    await _require_signature_config_permission(x_dms_principal)
    if body.folder_id is None and body.created_from is None and body.created_to is None:
        raise HTTPException(
            status_code=422, detail="Ordner oder Zeitraum angeben (kein unbegrenzter Nachlauf)"
        )
    if app.state.incoming.backfill_running():
        raise HTTPException(status_code=409, detail="Ein Nachlauf läuft bereits")
    status_ = app.state.incoming.start_backfill(
        folder_id=body.folder_id,
        created_from=body.created_from,
        created_to=body.created_to,
        only_unvalidated=body.only_unvalidated,
        requested_by=x_dms_principal,
    )
    return BackfillStatusOut(**status_.as_dict())


@app.get("/incoming-signatures/backfill", response_model=BackfillStatusOut)
async def get_incoming_signature_backfill(
    x_dms_principal: str = Header(default=""),
) -> BackfillStatusOut:
    await _require_signature_config_permission(x_dms_principal)
    return BackfillStatusOut(
        **app.state.incoming.backfill_status.as_dict(),
        queue_pending=app.state.incoming.worker.pending(),
    )


@app.get("/incoming-signatures/reports/{run_id}")
async def get_incoming_signature_report(
    run_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """The ETSI TS 119 102-2 validation report of one run (XML), the
    evidence behind the result. Needs `document.read` on the document."""
    try:
        row = await incoming_repository.get_run(session, run_id)
    except incoming_repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_document_read_permission(x_dms_principal, row.document_id)
    if row.report_key is None:
        raise HTTPException(status_code=404, detail="Für diesen Prüflauf liegt kein Bericht vor")
    data = await app.state.storage.download(row.report_key)
    filename = f"pruefbericht-{row.document_id}-v{row.version_number}-{run_id[:8]}.xml"
    return Response(
        content=data,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/incoming-signatures/trusted-lists", response_model=TrustedListsOut)
async def get_trusted_lists(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> TrustedListsOut:
    """Stored EU trusted lists with sequence numbers and dates, and the
    latest fetch or import."""
    await _require_signature_config_permission(x_dms_principal)
    lists = await incoming_repository.list_trusted_lists(session)
    last = await incoming_repository.last_fetch(session)
    return TrustedListsOut(
        lists=[TrustedListOut.model_validate(item) for item in lists],
        last_fetch=(
            {
                "started_at": last.started_at,
                "finished_at": last.finished_at,
                "source": last.source,
                "lists_stored": last.lists_stored,
                "errors": last.errors,
                "triggered_by": last.triggered_by,
            }
            if last
            else None
        ),
        poll_interval_seconds=settings.trusted_list_poll_interval_seconds,
        territories=settings.trusted_list_territories,
    )


@app.post("/incoming-signatures/trusted-lists/refresh", status_code=status.HTTP_202_ACCEPTED)
async def refresh_trusted_lists(x_dms_principal: str = Header(default="")) -> dict:
    """Starts a fetch now, in the background (it takes a while)."""
    await _require_signature_config_permission(x_dms_principal)
    task = asyncio.create_task(
        app.state.incoming.refresh_trusted_lists(triggered_by=x_dms_principal)
    )
    app.state.background_tasks.add(task)
    task.add_done_callback(app.state.background_tasks.discard)
    return {"started": True}


@app.post("/incoming-signatures/trusted-lists/import", response_model=TrustedListOut)
async def import_trusted_list(
    file: UploadFile = File(...),
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> TrustedListOut:
    """Offline import of one list (XML): the EU list of the lists first,
    then national lists. The same signature checks as a fetch."""
    await _require_signature_config_permission(x_dms_principal)
    data = await file.read(settings.trusted_list_max_bytes + 1)
    if len(data) > settings.trusted_list_max_bytes:
        raise HTTPException(status_code=413, detail="Datei zu groß")
    try:
        xml = data.decode("utf-8")
        info = await app.state.incoming.import_trusted_list(xml, triggered_by=x_dms_principal)
    except (UnicodeDecodeError, TrustedListError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    lists = await incoming_repository.list_trusted_lists(session)
    stored = next(i for i in lists if i.kind == info.kind and i.territory == info.territory)
    return TrustedListOut.model_validate(stored)
