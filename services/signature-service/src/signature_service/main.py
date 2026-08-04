import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, status
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
from signature_service.schemas import SignatureCreate, SignatureOut, VerificationOut
from signature_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_LEVEL_RANK = {"ses": 0, "aes": 1, "qes": 2}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS signature"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    async with app.state.session_factory() as session:
        ca = await repository.get_or_create_ca(session)
        await session.commit()
        ca_certificate_pem, ca_private_key_pem = ca.certificate_pem, ca.private_key_pem

    app.state.connectors = build_connectors(
        settings, ca_certificate_pem=ca_certificate_pem, ca_private_key_pem=ca_private_key_pem
    )
    app.state.document_client = DocumentServiceClient(settings.document_service_base_url)
    app.state.object_type_client = ObjectTypeServiceClient(settings.object_type_service_base_url)
    app.state.auth_client = AuthServiceClient(
        settings.auth_service_base_url,
        admin_username=settings.auth_service_admin_username,
        admin_password=settings.auth_service_admin_password,
    )

    producer = NatsEventBusClient(settings.nats_url, stream="signature")
    await producer.connect()
    app.state.producer = producer

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
    await producer.close()
    await app.state.document_client.close()
    await app.state.object_type_client.close()
    await app.state.auth_client.close()
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
    await app.state.producer.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.post("/signatures", response_model=SignatureOut, status_code=status.HTTP_201_CREATED)
async def create_signature(
    payload: SignatureCreate, session: AsyncSession = Depends(get_session)
) -> SignatureOut:
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

    resolved = resolve_connector_for_level(settings, app.state.connectors, payload.level)
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
