import asyncio
import contextlib
import email
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

import aiosmtplib
from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, status
from mail_connector import matching, repository
from mail_connector.backends import RawIncomingMessage, build_backend
from mail_connector.case_client import CaseClient
from mail_connector.document_client import DocumentClient
from mail_connector.models import Base
from mail_connector.object_type_client import ObjectTypeClient
from mail_connector.schemas import (
    AssignRequest,
    ConfirmMatchRequest,
    InboundAttachmentOut,
    InboundMessageOut,
    OutboundMessageCreate,
    OutboundMessageOut,
    RejectRequest,
)
from mail_connector.settings import Settings
from mail_connector.storage_client import ObjectNotFoundError, StorageClient
from mail_connector.virus_scan_client import VirusScanClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

_FILENAME_SANITIZE_RE = re.compile(r"[^\w.\- ]+")


def _has_poststelle_role(x_dms_roles: str) -> bool:
    roles = {role.strip() for role in x_dms_roles.split(",") if role.strip()}
    return settings.poststelle_role in roles


def _parse_message(
    raw_bytes: bytes,
) -> tuple[str, str, datetime, str, list[tuple[str, str | None, bytes]]]:
    """Zerlegt eine rohe RFC-822-Nachricht (2.5/3.3) in Kopfdaten, Textkörper
    und Anhänge. Der Textkörper wird selbst wie ein (synthetischer) Anhang
    behandelt - siehe `_ingest_message` - damit er denselben verpflichtenden
    Virenscan (10.3) durchläuft wie jeder echte Dateianhang."""
    msg = email.message_from_bytes(raw_bytes)
    from_address = msg.get("From", "unbekannt")
    subject = msg.get("Subject", "(kein Betreff)")
    date_header = msg.get("Date")
    try:
        received_at = parsedate_to_datetime(date_header) if date_header else datetime.now(UTC)
    except (TypeError, ValueError):
        received_at = datetime.now(UTC)
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)

    body_text = ""
    attachments: list[tuple[str, str | None, bytes]] = []
    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disposition = part.get_content_disposition()
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            if disposition == "attachment":
                filename = part.get_filename() or "anhang"
                attachments.append((filename, part.get_content_type(), payload))
            elif part.get_content_type() == "text/plain" and not body_text:
                charset = part.get_content_charset() or "utf-8"
                body_text += payload.decode(charset, errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload is not None:
            charset = msg.get_content_charset() or "utf-8"
            body_text = payload.decode(charset, errors="replace")

    return from_address, subject, received_at, body_text, attachments


def _safe_filename(name: str) -> str:
    return _FILENAME_SANITIZE_RE.sub("_", name).strip() or "unbenannt"


async def _ingest_message(session: AsyncSession, raw: RawIncomingMessage) -> None:
    from_address, subject, received_at, body_text, attachment_parts = _parse_message(raw.raw_bytes)

    # Kandidaten-Muster frisch je Nachricht statt einmalig beim Start
    # zwischengespeichert (Post-Roadmap Phase 19 Session 11) - Posteingang
    # ist realistisch niedrigfrequent (Poststelle-Betrieb), ein zusätzlicher
    # Cross-Service-Aufruf je NEU eingehender Nachricht (nicht je Kandidat)
    # ist vertretbar und hält neu angelegte Objekttypen/geänderte Formate
    # sofort wirksam, ohne einen Neustart abzuwarten.
    candidate_pattern = await _load_candidate_pattern()
    match = await matching.resolve_match(
        f"{subject}\n{body_text}",
        document_client=app.state.documents,
        case_client=app.state.cases,
        pattern=candidate_pattern,
    )
    message = await repository.create_inbound_message(
        session,
        source_uid=raw.uid,
        from_address=from_address,
        subject=subject,
        body_text=body_text,
        received_at=received_at,
        match_type=match.match_type,
        match_value=match.match_value,
        proposed_target_type=match.target_type,
        proposed_target_id=match.target_id,
        match_candidates=match.candidates,
    )

    # Der Textkörper selbst zählt als erster (synthetischer) Anhang - die
    # Korrespondenz an sich ist genauso archivierungswürdig wie ihre
    # Beilagen (siehe Docstring an `_parse_message`).
    parts = list(attachment_parts)
    if body_text.strip():
        parts.insert(0, (f"{_safe_filename(subject)}.txt", "text/plain", body_text.encode("utf-8")))

    for filename, content_type, payload in parts:
        scan = await app.state.virus_scan.scan(
            data=payload, filename=filename, content_type=content_type, created_by="mail-connector"
        )
        storage_key = None
        if scan.status == "clean":
            storage_key = f"posteingang/{message.id}/{uuid.uuid4().hex}/{filename}"
            await app.state.storage.upload(storage_key, payload, content_type)
        await repository.add_attachment(
            session,
            message_id=message.id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(payload),
            scan_id=scan.scan_id,
            scan_status=scan.status,
            storage_object_key=storage_key,
        )

    await publish_event(
        "mail_connector.message.received",
        subject=message.id,
        payload={
            "from_address": from_address,
            "subject": subject,
            "status": message.status,
            "match_type": message.match_type,
        },
    )


async def _load_candidate_pattern() -> "re.Pattern[str]":
    """Post-Roadmap Phase 19 Session 11 - liest die tatsächlich
    konfigurierten `kennzeichen_format`-Werte (object-type-service, je
    Objekttyp) und das globale `case_number_config.format` (case-service)
    und baut daraus das Kandidaten-Muster (matching.build_candidate_
    pattern). Best-Effort, aufgerufen je neu eingehender Nachricht (siehe
    `_ingest_message`), nicht einmalig beim Start - Posteingang ist
    niedrigfrequent genug, dass ein neu angelegter Objekttyp/geändertes
    Format ohne Neustart sofort wirksam wird. Schlägt ein Service fehl,
    bleibt es für diese eine Nachricht beim generischen Rückfall-Muster.
    Bewusst EIGENE, kurzlebige Clients statt der langlebigen `app.state.*`-
    Instanzen: Letztere sind an den Event-Loop gebunden, in dem sie beim
    Lifespan-Start erzeugt wurden - ein direkter Testaufruf von
    `_ingest_message()` (an `TestClient`s eigenem Request-Dispatch vorbei,
    siehe `tests/test_api.py::_ingest`) läuft in einem ANDEREN Loop
    (pytest-asyncio) und würde beim ersten Zugriff mit "bound to a
    different event loop" fehlschlagen - dasselbe bereits andernorts im
    Projekt dokumentierte asyncpg/pytest-asyncio-Muster, hier für httpx."""
    formats: list[str] = []
    object_type_client = ObjectTypeClient(settings.object_type_service_base_url)
    try:
        formats.extend(await object_type_client.list_kennzeichen_formats())
    except Exception:
        logger.warning(
            "kennzeichen_format-Abfrage bei object-type-service fehlgeschlagen - "
            "Kandidaten-Muster nutzt insoweit den generischen Rückfall."
        )
    finally:
        await object_type_client.close()

    case_client = CaseClient(settings.case_service_base_url)
    try:
        number_config = await case_client.get_number_config()
        if number_config.get("format"):
            formats.append(number_config["format"])
    except Exception:
        logger.warning(
            "case_number_config-Abfrage bei case-service fehlgeschlagen - "
            "Kandidaten-Muster nutzt insoweit den generischen Rückfall."
        )
    finally:
        await case_client.close()

    return matching.build_candidate_pattern(formats)


async def _poll_loop(session_factory) -> None:
    """Holt zyklisch neue Nachrichten ab (2.5/3.3) - gleiches Poll-Loop-Idiom
    wie document-service's `_retention_poll_loop` (ADR 0020), hier deutlich
    kürzer getaktet (Poststelle-Betrieb, siehe settings.py)."""
    while True:
        try:
            messages = await app.state.backend.fetch_new_messages()
            for raw in messages:
                async with session_factory() as session:
                    if await repository.get_by_source_uid(session, raw.uid) is not None:
                        continue
                    await _ingest_message(session, raw)
                    await session.commit()
        except Exception:
            logger.exception(
                "Posteingang-Poll-Tick fehlgeschlagen - wird beim naechsten Tick erneut versucht."
            )
        await asyncio.sleep(settings.poll_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS mail_connector"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.storage = StorageClient(settings.storage_service_base_url)
    app.state.virus_scan = VirusScanClient(settings.virus_scan_service_base_url)
    app.state.documents = DocumentClient(settings.document_service_base_url)
    app.state.cases = CaseClient(settings.case_service_base_url)
    app.state.backend = build_backend(settings)

    event_bus = NatsEventBusClient(settings.nats_url, stream="mail_connector")
    await event_bus.connect()
    app.state.event_bus = event_bus

    poll_task = asyncio.create_task(_poll_loop(app.state.session_factory))

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    startup_end = time.time()
    logger.info("Startup completed in %s ms.", round((startup_end - startup_start) * 1000, 3))

    yield

    poll_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await poll_task
    if registration:
        await registration.stop()
    await event_bus.close()
    await app.state.storage.close()
    await app.state.virus_scan.close()
    await app.state.documents.close()
    await app.state.cases.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


async def publish_event(event_type: str, subject: str, payload: dict) -> None:
    event = Event(
        event_type=event_type, service_name=settings.service_name, subject=subject, payload=payload
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


def _require_poststelle(x_dms_principal: str, x_dms_roles: str) -> None:
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="X-DMS-Principal fehlt")
    if not _has_poststelle_role(x_dms_roles):
        raise HTTPException(
            status_code=403,
            detail=f"Nur die Rolle {settings.poststelle_role!r} darf den Posteingang/Postausgang "
            "einsehen/bearbeiten",
        )


async def _to_message_out(session: AsyncSession, message) -> InboundMessageOut:
    attachments = await repository.list_attachments(session, message.id)
    base = InboundMessageOut.model_validate(message)
    return base.model_copy(
        update={"attachments": [InboundAttachmentOut.model_validate(a) for a in attachments]}
    )


@app.get("/inbound", response_model=list[InboundMessageOut])
async def list_inbound(
    status_filter: str | None = None,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[InboundMessageOut]:
    _require_poststelle(x_dms_principal, x_dms_roles)
    messages = await repository.list_messages(session, status=status_filter)
    return [await _to_message_out(session, m) for m in messages]


@app.get("/inbound/{message_id}", response_model=InboundMessageOut)
async def get_inbound(
    message_id: str,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> InboundMessageOut:
    _require_poststelle(x_dms_principal, x_dms_roles)
    try:
        message = await repository.get_message(session, message_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await _to_message_out(session, message)


async def _create_documents_for_message(
    session: AsyncSession, message, *, folder_id: str | None, created_by: str, title: str
) -> None:
    """`title` gilt für das ERSTE angelegte Dokument (deterministisch der
    Textkörper, siehe `_ingest_message`s `parts.insert(0, ...)`) - bei
    mehreren Anhängen behalten die übrigen ihren eigenen Dateinamen als
    Titel, da ein einzelner vom Formular vorgegebener Titel nicht sinnvoll
    auf mehrere unterschiedliche Anlagen zugleich passt."""
    for index, attachment in enumerate(await repository.list_attachments(session, message.id)):
        if attachment.scan_status != "clean" or attachment.storage_object_key is None:
            continue
        try:
            data = await app.state.storage.download(attachment.storage_object_key)
        except ObjectNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Zwischengelagerter Anhang {attachment.filename!r} nicht (mehr) vorhanden",
            ) from exc
        document = await app.state.documents.create_document(
            data=data,
            filename=attachment.filename,
            content_type=attachment.content_type,
            title=title if index == 0 else attachment.filename,
            created_by=created_by,
            folder_id=folder_id,
        )
        await app.state.storage.delete(attachment.storage_object_key)
        await repository.set_attachment_document(session, attachment.id, document_id=document["id"])
        if message.proposed_target_type == "case" and message.proposed_target_id is not None:
            await app.state.cases.add_document_reference(
                message.proposed_target_id, document_id=document["id"], added_by=created_by
            )


@app.post("/inbound/{message_id}/confirm-match", response_model=InboundMessageOut)
async def confirm_match(
    message_id: str,
    payload: ConfirmMatchRequest,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> InboundMessageOut:
    """Bestätigt einen vorgeschlagenen Treffer (2.5, P15-S3) - keine stille
    Zuordnung: der Treffer wurde beim Abholen nur VORGESCHLAGEN
    (`status="proposed_match"`), erst dieser Aufruf legt tatsächlich
    Dokumente an."""
    _require_poststelle(x_dms_principal, x_dms_roles)
    try:
        message = await repository.get_message(session, message_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if message.status != "proposed_match":
        raise HTTPException(
            status_code=409,
            detail=f"Nachricht befindet sich nicht in status='proposed_match' "
            f"(aktuell {message.status!r})",
        )

    folder_id = payload.folder_id
    if folder_id is None:
        if message.proposed_target_type == "document":
            document = await app.state.documents.get(message.proposed_target_id)
            if document is None:
                raise HTTPException(
                    status_code=409, detail="Zugeordnetes Dokument nicht mehr auffindbar"
                )
            # Fällt zusammen mit dem Ordner des gefundenen Dokuments (naheliegender
            # Default: eine Rückmeldung zu einem bekannten Dokument wird neben
            # diesem abgelegt) - `None`, wenn jenes Dokument selbst auf oberster
            # Ebene liegt, dann landet die neue Post ebenfalls dort.
            folder_id = document["folder_id"]
        else:
            raise HTTPException(
                status_code=400,
                detail="folder_id ist bei einer Vorgangsnummer-Zuordnung erforderlich",
            )

    await _create_documents_for_message(
        session, message, folder_id=folder_id, created_by=x_dms_principal, title=payload.title
    )
    message = await repository.mark_confirmed(session, message_id, confirmed_by=x_dms_principal)
    await session.commit()
    await publish_event(
        "mail_connector.message.confirmed",
        subject=message_id,
        payload={"confirmed_by": x_dms_principal, "folder_id": folder_id},
    )
    return await _to_message_out(session, message)


@app.post("/inbound/{message_id}/assign", response_model=InboundMessageOut)
async def assign_manually(
    message_id: str,
    payload: AssignRequest,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> InboundMessageOut:
    """Manuelle Zuordnung (2.5) - für Nachrichten ohne (oder mit mehrdeutigem)
    automatischem Treffer, aber auch als bewusste Übersteuerung eines
    vorgeschlagenen Treffers nutzbar (z. B. falsches Kennzeichen erkannt)."""
    _require_poststelle(x_dms_principal, x_dms_roles)
    try:
        message = await repository.get_message(session, message_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if message.status not in ("unassigned", "proposed_match"):
        raise HTTPException(
            status_code=409,
            detail=f"Nachricht ist bereits abschließend bearbeitet (status={message.status!r})",
        )

    if payload.case_id is not None:
        case = await app.state.cases.get(payload.case_id)
        if case is None:
            raise HTTPException(status_code=400, detail=f"case_id {payload.case_id!r} unbekannt")
        message.proposed_target_type = "case"
        message.proposed_target_id = payload.case_id

    await _create_documents_for_message(
        session,
        message,
        folder_id=payload.folder_id,
        created_by=x_dms_principal,
        title=payload.title,
    )
    message = await repository.mark_confirmed(session, message_id, confirmed_by=x_dms_principal)
    await session.commit()
    await publish_event(
        "mail_connector.message.confirmed",
        subject=message_id,
        payload={"confirmed_by": x_dms_principal, "folder_id": payload.folder_id, "manual": True},
    )
    return await _to_message_out(session, message)


@app.post("/inbound/{message_id}/reject", response_model=InboundMessageOut)
async def reject_message(
    message_id: str,
    payload: RejectRequest,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> InboundMessageOut:
    _require_poststelle(x_dms_principal, x_dms_roles)
    try:
        message = await repository.get_message(session, message_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if message.status not in ("unassigned", "proposed_match"):
        raise HTTPException(
            status_code=409,
            detail=f"Nachricht ist bereits abschließend bearbeitet (status={message.status!r})",
        )
    message = await repository.mark_rejected(
        session, message_id, rejected_by=x_dms_principal, reason=payload.reason
    )
    await session.commit()
    return await _to_message_out(session, message)


@app.post("/outbound", response_model=OutboundMessageOut, status_code=status.HTTP_201_CREATED)
async def send_outbound(
    payload: OutboundMessageCreate,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> OutboundMessageOut:
    _require_poststelle(x_dms_principal, x_dms_roles)
    message = EmailMessage()
    message["From"] = settings.smtp_from_address
    message["To"] = payload.to_address
    message["Subject"] = payload.subject
    message.set_content(payload.body)

    error_message = None
    send_status = "sent"
    try:
        client = aiosmtplib.SMTP(
            hostname=settings.smtp_host, port=settings.smtp_port, start_tls=settings.smtp_use_tls
        )
        async with client:
            if settings.smtp_username is not None:
                await client.login(settings.smtp_username, settings.smtp_password or "")
            await client.send_message(message)
    except (aiosmtplib.SMTPException, OSError) as exc:
        send_status = "failed"
        error_message = str(exc)

    record = await repository.create_outbound_message(
        session,
        to_address=payload.to_address,
        subject=payload.subject,
        body=payload.body,
        related_document_id=payload.related_document_id,
        related_case_id=payload.related_case_id,
        sent_by=x_dms_principal,
        status=send_status,
        error_message=error_message,
    )
    await session.commit()
    await publish_event(
        "mail_connector.message.sent"
        if send_status == "sent"
        else "mail_connector.message.send_failed",
        subject=record.id,
        payload={"to_address": payload.to_address, "sent_by": x_dms_principal},
    )
    return record


@app.get("/outbound", response_model=list[OutboundMessageOut])
async def list_outbound(
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[OutboundMessageOut]:
    _require_poststelle(x_dms_principal, x_dms_roles)
    return await repository.list_outbound_messages(session)
