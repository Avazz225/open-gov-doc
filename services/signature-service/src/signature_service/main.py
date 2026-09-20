import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    http_sensor_declarations,
    metrics_payload,
)
from dms_permission_client import PermissionServiceClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from signature_service import repository
from signature_service.auth_client import AuthServiceClient
from signature_service.connectors import build_connectors, resolve_connector_for_level
from signature_service.document_client import DocumentServiceClient, LockConflictError
from signature_service.document_client import NotFoundError as DocumentNotFoundError
from signature_service.models import Base
from signature_service.object_type_client import NotFoundError as ObjectTypeNotFoundError
from signature_service.object_type_client import ObjectTypeServiceClient
from signature_service.schemas import (
    SignatureCreate,
    SignatureOut,
    SignatureProviderLevelsIn,
    SignatureProviderStatusOut,
    VerificationOut,
)
from signature_service.settings import Settings, SignatureProviderConfig

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

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    app.state.retimestamp_task.cancel()
    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    if registration:
        await registration.stop()
    await producer.close()
    await app.state.document_client.close()
    await app.state.object_type_client.close()
    await app.state.auth_client.close()
    await app.state.permission_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)

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


@app.post("/signatures", response_model=SignatureOut, status_code=status.HTTP_201_CREATED)
async def create_signature(
    payload: SignatureCreate,
    session: AsyncSession = Depends(get_session),
    x_dms_maintenance_active: str = Header(default="false"),
) -> SignatureOut:
    await _reject_during_maintenance(x_dms_maintenance_active)
    try:
        document = await app.state.document_client.get_document(payload.document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

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
            created_by=signer.display_name,
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
    document_id: str | None = None, session: AsyncSession = Depends(get_session)
) -> list[SignatureOut]:
    return await repository.list_signatures(session, document_id=document_id)


@app.get("/signatures/{signature_id}", response_model=SignatureOut)
async def get_signature(
    signature_id: int, session: AsyncSession = Depends(get_session)
) -> SignatureOut:
    try:
        return await repository.get_signature(session, signature_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/signatures/{signature_id}/verify", response_model=VerificationOut)
async def verify_signature(
    signature_id: int, session: AsyncSession = Depends(get_session)
) -> VerificationOut:
    try:
        signature = await repository.get_signature(session, signature_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

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
