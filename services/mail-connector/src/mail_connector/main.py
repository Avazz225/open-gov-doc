import asyncio
import email
import logging
import os
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
from dms_common.otel import setup_observability
from dms_common.shutdown import bounded_shutdown_step
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_metrics_client import (
    SensorConfigClient,
    bootstrap_http_sensors,
    metrics_payload,
)
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, Response, status
from mail_connector import crypto, mailboxes, matching, repository
from mail_connector.backends import RawIncomingMessage, build_backend
from mail_connector.case_client import CaseClient
from mail_connector.document_client import DocumentClient, StructureRefusedError
from mail_connector.models import Base
from mail_connector.object_type_client import ObjectTypeClient
from mail_connector.permission_client import PermissionServiceClient
from mail_connector.schemas import (
    AssignRequest,
    ConfirmMatchRequest,
    InboundAttachmentOut,
    InboundMessageOut,
    MailboxConfigIn,
    MailboxConfigOut,
    MailboxOut,
    MailboxTestIn,
    MailboxTestResult,
    MailRoutingLogEntryOut,
    MailRoutingLogEntryWithMessageOut,
    OutboundMessageCreate,
    OutboundMessageOut,
    RejectRequest,
    RouteMessageRequest,
)
from mail_connector.settings import Settings
from mail_connector.signature_pairing import pair_detached_signatures
from mail_connector.storage_client import ObjectNotFoundError, StorageClient
from mail_connector.virus_scan_client import VirusScanClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
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
    """Parses a raw RFC-822 message (2.5/3.3) into headers, body text, and
    attachments. The body text itself is treated like a (synthetic)
    attachment - see `_ingest_message` - so it goes through the same
    mandatory virus scan (10.3) as any real file attachment."""
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


async def _ingest_message(session: AsyncSession, mailbox_id: str, raw: RawIncomingMessage) -> None:
    """P53-S4: every client used in this function's own body is constructed
    fresh here and closed in the `finally` below, instead of reading the
    long-lived `app.state.documents`/`app.state.cases`/`app.state.virus_scan`/
    `app.state.storage` instances - the same already-established pattern
    `_load_candidate_pattern` uses for its own two clients (see that
    docstring for the full explanation: an `app.state.*` client is bound to
    the event loop it was first used on at lifespan startup). This function
    is the one genuinely ambiguous call site in this service - called both
    from the background `_poll_loop` task (always running on the lifespan/
    portal event loop) AND directly from tests bypassing `TestClient`'s own
    request dispatch (`tests/test_api.py::_ingest`, pytest-asyncio's own,
    different event loop) - so, unlike every other endpoint in this file
    (always reached via one real HTTP call, always on the same loop lifespan
    started on), it cannot safely assume which loop it runs on. This was the
    root cause of this service's long-standing, non-deterministic
    `RuntimeError: ... bound to a different event loop` test flakiness
    (Post-Roadmap Phase 31 Session 12, Phase 38 Session 4, Phase 44 Session
    3 all re-confirmed it without fixing it at the root) - the same root
    cause also affects `app.state.event_bus` (NATS), fixed further down in
    this function with the identical pattern; there it surfaces not as a
    `RuntimeError` but as an unexplained `nats.errors.TimeoutError`, since a
    reply `Future` awaited from a different loop than the one whose
    background read-loop task resolves it just never wakes up instead of
    raising immediately."""
    # ADR 0189/P61-S4: checked against the RAW byte length BEFORE
    # `_parse_message` is even called - see `settings.max_message_size_bytes`'s
    # own docstring for why this is the right point to reject, rather than
    # during/after MIME parsing. The message row is still created (marked
    # `rejected` immediately) so the mailbox's own idempotency check
    # (`get_by_source_uid`, this function's only caller) does not retry it
    # forever.
    if len(raw.raw_bytes) > settings.max_message_size_bytes:
        logger.warning(
            "inbound_message_exceeds_max_size mailbox_id=%s uid=%s size=%d limit=%d",
            mailbox_id,
            raw.uid,
            len(raw.raw_bytes),
            settings.max_message_size_bytes,
        )
        message = await repository.create_inbound_message(
            session,
            mailbox_id=mailbox_id,
            source_uid=raw.uid,
            from_address="unbekannt",
            subject="(Nachricht überschreitet die maximal zulässige Größe)",
            body_text="",
            received_at=datetime.now(UTC),
            match_type=None,
            match_value=None,
            proposed_target_type=None,
            proposed_target_id=None,
            match_candidates=[],
        )
        await repository.mark_rejected(
            session,
            message.id,
            rejected_by="mail-connector",
            reason=(
                f"Nachricht ({len(raw.raw_bytes)} Bytes) überschreitet die maximal zulässige "
                f"Größe von {settings.max_message_size_bytes} Bytes - automatische Verarbeitung "
                "(MIME-Parsing, Virenscan) wurde nicht durchgeführt."
            ),
        )
        await session.commit()
        return

    from_address, subject, received_at, body_text, attachment_parts = _parse_message(raw.raw_bytes)

    document_client = DocumentClient(settings.document_service_base_url)
    case_client = CaseClient(settings.case_service_base_url)
    virus_scan_client = VirusScanClient(settings.virus_scan_service_base_url)
    storage_client = StorageClient(settings.storage_service_base_url)
    try:
        # Candidate pattern loaded fresh per message instead of cached once at
        # startup (Post-Roadmap Phase 19 Session 11) - inbound mail is
        # realistically low-frequency (mail room operation), an additional
        # cross-service call per NEWLY incoming message (not per candidate) is
        # acceptable and keeps newly created object types/changed formats
        # immediately effective, without waiting for a restart.
        candidate_pattern = await _load_candidate_pattern()
        match = await matching.resolve_match(
            f"{subject}\n{body_text}",
            document_client=document_client,
            case_client=case_client,
            pattern=candidate_pattern,
        )
        message = await repository.create_inbound_message(
            session,
            mailbox_id=mailbox_id,
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

        # The body text itself counts as the first (synthetic) attachment - the
        # correspondence itself is just as worth archiving as its enclosures
        # (see docstring on `_parse_message`).
        parts = list(attachment_parts)
        if body_text.strip():
            parts.insert(
                0, (f"{_safe_filename(subject)}.txt", "text/plain", body_text.encode("utf-8"))
            )

        for filename, content_type, payload in parts:
            scan = await virus_scan_client.scan(
                data=payload,
                filename=filename,
                content_type=content_type,
                created_by="mail-connector",
            )
            storage_key = None
            if scan.status == "clean":
                storage_key = f"posteingang/{message.id}/{uuid.uuid4().hex}/{filename}"
                await storage_client.upload(storage_key, payload, content_type)
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
    finally:
        await document_client.close()
        await case_client.close()
        await virus_scan_client.close()
        await storage_client.close()

    # Same reasoning as the four clients above - `app.state.event_bus`'s
    # underlying NATS connection was `connect()`-ed at lifespan startup (the
    # portal loop), and its request/reply reply is resolved by a background
    # read-loop task bound to THAT loop; awaiting it from a different loop
    # (a direct test call) leaves the waiting `Future` never woken and
    # surfaces as an unexplained `nats.errors.TimeoutError` instead of the
    # httpx clients' `RuntimeError` - found live while writing this
    # session's own regression test (two real, sequential `_ingest()` calls
    # in one test), see `test_two_back_to_back_ingests_do_not_hit_the_cross_event_loop_error`.
    ingest_event_bus = NatsEventBusClient(settings.nats_url, stream="mail_connector")
    await ingest_event_bus.connect()
    try:
        await publish_event(
            "mail_connector.message.received",
            subject=message.id,
            payload={
                "from_address": from_address,
                "subject": subject,
                "status": message.status,
                "match_type": message.match_type,
            },
            event_bus=ingest_event_bus,
        )
    finally:
        await ingest_event_bus.close()


async def _load_candidate_pattern() -> "re.Pattern[str]":
    """Post-Roadmap Phase 19 Session 11 - reads the actually configured
    `kennzeichen_format` values (object-type-service, per object type) and
    the global `case_number_config.format` (case-service) and builds the
    candidate pattern from them (matching.build_candidate_pattern).
    Best-effort, called per newly incoming message (see `_ingest_message`),
    not once at startup - inbound mail is low-frequency enough that a newly
    created object type/changed format takes effect immediately without a
    restart. If a service fails, this one message falls back to the generic
    fallback pattern. Deliberately uses its OWN, short-lived clients instead
    of the long-lived `app.state.*` instances: the latter are bound to the
    event loop in which they were created at lifespan startup - a direct
    test call to `_ingest_message()` (bypassing `TestClient`'s own request
    dispatch, see `tests/test_api.py::_ingest`) runs in a DIFFERENT loop
    (pytest-asyncio) and would fail on first access with "bound to a
    different event loop" - the same asyncpg/pytest-asyncio pattern already
    documented elsewhere in the project, here for httpx."""
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


async def _ingest_message_or_skip_duplicate(
    session: AsyncSession, mailbox_id: str, raw: RawIncomingMessage
) -> None:
    """P62-S1: `_poll_loop`'s own `get_by_source_uid` idempotency check
    (this function's only caller) is check-then-act, not atomic - only
    reachable with >1 replica polling the same mailbox concurrently (this
    service currently runs single-replica, so this is a defensive guard,
    not a currently-observed failure mode). `create_inbound_message`'s own
    `(mailbox_id, source_uid)` unique constraint is the real safety net
    against an actual duplicate row - this just turns the inevitable
    loser's error from a scary, uncaught-looking stack trace into an
    expected, logged no-op. Investigated whether the losing replica could
    leave an orphaned storage object (the originally suspected risk here) -
    it can't with `_ingest_message`'s actual statement order:
    `create_inbound_message`'s `INSERT` is flushed, and its
    unique-constraint conflict raised, BEFORE any virus-scan/storage-upload
    call - a losing replica never reaches storage at all."""
    try:
        await _ingest_message(session, mailbox_id, raw)
        await session.commit()
    except IntegrityError:
        await session.rollback()
        logger.info(
            "inbound_message_already_ingested_by_another_replica mailbox_id=%s uid=%s",
            mailbox_id,
            raw.uid,
        )


async def _poll_loop(session_factory) -> None:
    """Cyclically retrieves new messages (2.5/3.3) - same poll-loop idiom as
    document-service's `_retention_poll_loop` (ADR 0020), here at a
    considerably shorter cadence (mail room operation, see settings.py).
    Since Post-Roadmap Phase 31 Session 12a, one tick polls every configured
    mailbox in turn (`app.state.backends`, `dict[mailbox_id,
    MailboxBackend]`) - a failure fetching/ingesting one mailbox does not
    prevent the others from being polled in the same tick (each mailbox's
    `try` is independent, not the whole loop's). Since Post-Roadmap Phase
    44 Session 3 (4.8, ADR 0164): skips the whole tick while maintenance
    mode is active - ADR 0152 named this loop explicitly as "a full
    document-creation pipeline off a POP3 timer," exactly the kind of
    write an emergency lockdown exists to stop, with zero gateway
    involvement ever. This loop's `try` is per-mailbox, not a single
    enclosing one - the check gets its own try/except instead, failing
    OPEN (proceeds with the tick) on error, same principle the gateway's
    own maintenance check already uses (ADR 0024). Unwrapped, a transient
    `permission-service` error here was found to kill the whole task
    outright during this session's own test run (surfaced as a
    cross-event-loop `RuntimeError` in an unrelated test once the
    now-dead background task's stale client got garbage-collected)."""
    while True:
        await _poll_tick(session_factory)
        await asyncio.sleep(settings.poll_interval_seconds)


async def _poll_tick(session_factory) -> None:
    """One pass over every enabled mailbox (split out of `_poll_loop` in
    P88-S4a so a test can run exactly one tick on the app's own loop)."""
    try:
        skip_tick = await app.state.permission_client.is_maintenance_active()
    except Exception:
        logger.exception("Wartungsmodus-Check fehlgeschlagen - Tick wird trotzdem ausgefuehrt.")
        skip_tick = False
    if skip_tick:
        return

    # P88-S4a (ADR 0244): the mailbox list is read from the table on
    # every tick, so an admin change takes effect without a restart.
    try:
        async with session_factory() as session:
            rows = [row for row in await mailboxes.list_rows(session) if row.enabled]
    except Exception:
        logger.exception("Postfachliste nicht lesbar - Tick uebersprungen.")
        rows = []
    for row in rows:
        mailbox_id = row.id
        try:
            config = mailboxes.to_config(row, app.state.credentials_key)
            backend = app.state.backend_factory(config)
            messages = await backend.fetch_new_messages()
            for raw in messages:
                async with session_factory() as session:
                    if await repository.get_by_source_uid(session, mailbox_id, raw.uid) is not None:
                        continue
                    await _ingest_message_or_skip_duplicate(session, mailbox_id, raw)
            _record_poll(mailbox_id, None)
        except Exception as exc:
            _record_poll(mailbox_id, exc)
            logger.exception(
                "Posteingang-Poll-Tick fuer Postfach %r fehlgeschlagen - wird beim "
                "naechsten Tick erneut versucht.",
                mailbox_id,
            )


def _record_poll(mailbox_id: str, error: Exception | None) -> None:
    """Last poll result per mailbox, in this replica's memory only - shown
    on the admin page next to the mailbox (P88-S4a). Never contains the
    password: exceptions from poplib/imaplib carry the server's reply."""
    app.state.poll_status[mailbox_id] = {
        "at": datetime.now(UTC),
        "ok": error is None,
        "error": None if error is None else f"{type(error).__name__}: {error}"[:500],
    }


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS mail_connector"))
        await conn.run_sync(Base.metadata.create_all)
        # Multi-inbox model (14.2, Post-Roadmap Phase 31 Session 12a) - ad
        # hoc migration (no Alembic, see CONTRIBUTING.md): `mailbox_id` is
        # new, backfilled to `"central"` (the fixed id of the single default
        # mailbox this session's `Settings.mailboxes` default reproduces),
        # then the old single-column unique constraint on `source_uid`
        # alone is replaced with the new composite one (see
        # `models.InboundMessage`) - guarded so a fresh install (where
        # `create_all` above already created the composite constraint
        # directly) does no-ops on every check.
        await conn.execute(
            text(
                "ALTER TABLE mail_connector.inbound_message "
                "ADD COLUMN IF NOT EXISTS mailbox_id VARCHAR(128)"
            )
        )
        await conn.execute(
            text(
                "UPDATE mail_connector.inbound_message SET mailbox_id = 'central' "
                "WHERE mailbox_id IS NULL"
            )
        )
        mailbox_id_nullable = await conn.execute(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'mail_connector' AND table_name = 'inbound_message' "
                "AND column_name = 'mailbox_id'"
            )
        )
        if mailbox_id_nullable.scalar() == "YES":
            await conn.execute(
                text(
                    "ALTER TABLE mail_connector.inbound_message "
                    "ALTER COLUMN mailbox_id SET NOT NULL"
                )
            )
        legacy_constraint = await conn.execute(
            text(
                "SELECT 1 FROM information_schema.table_constraints "
                "WHERE table_schema = 'mail_connector' AND table_name = 'inbound_message' "
                "AND constraint_name = 'inbound_message_source_uid_key'"
            )
        )
        if legacy_constraint.scalar() is not None:
            await conn.execute(
                text(
                    "ALTER TABLE mail_connector.inbound_message "
                    "DROP CONSTRAINT inbound_message_source_uid_key"
                )
            )
            await conn.execute(
                text(
                    "ALTER TABLE mail_connector.inbound_message "
                    "ADD CONSTRAINT uq_inbound_message_mailbox_source "
                    "UNIQUE (mailbox_id, source_uid)"
                )
            )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.storage = StorageClient(settings.storage_service_base_url)
    app.state.virus_scan = VirusScanClient(settings.virus_scan_service_base_url)
    app.state.documents = DocumentClient(settings.document_service_base_url)
    app.state.cases = CaseClient(settings.case_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    # P88-S4a (ADR 0244): mailboxes live in the table. The key encrypts
    # their passwords; without it, existing mailboxes can't be polled and
    # none with a password can be saved - the service still starts, so the
    # rest of the mail room keeps working.
    try:
        app.state.credentials_key = crypto.load_key(settings.mailbox_credentials_key)
    except crypto.CredentialKeyError as exc:
        app.state.credentials_key = None
        logger.error("Postfach-Zugangsdaten nicht nutzbar: %s", exc)
    app.state.backend_factory = build_backend
    app.state.poll_status = {}
    async with app.state.session_factory() as session:
        try:
            adopted = await mailboxes.adopt_from_settings(
                session, settings.mailboxes, app.state.credentials_key
            )
            await session.commit()
        except crypto.CredentialKeyError as exc:
            adopted = None
            logger.error("DMS_MAILBOXES nicht übernommen: %s", exc)
        except IntegrityError:
            # Another replica adopted at the same moment.
            await session.rollback()
            adopted = None
    if adopted is not None:
        logger.info("mailboxes_adopted_from_env", extra={"mailbox_ids": adopted})
    elif os.environ.get("DMS_MAILBOXES"):
        logger.warning(
            "DMS_MAILBOXES ist gesetzt, wird aber ignoriert: Postfächer werden seit P88-S4a "
            "über die Admin-Oberfläche verwaltet (ADR 0244)."
        )

    event_bus = NatsEventBusClient(settings.nats_url, stream="mail_connector")
    await event_bus.connect()
    app.state.event_bus = event_bus

    # Sensor concept (10.1, full rollout) - a fresh `SensorConfigClient` per
    # startup, bound into the module-level `sensor_config_proxy` (built by
    # `bootstrap_http_sensors` right after `app = FastAPI(...)`), not a
    # module-level client itself: its httpx client can't outlive the event
    # loop it was first used on (same pattern as document-service/
    # registry-service).
    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    poll_task = asyncio.create_task(_poll_loop(app.state.session_factory))

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        sensors=sensor_registry.declarations(),
    )

    startup_end = time.time()
    logger.info("Startup completed in %s ms.", round((startup_end - startup_start) * 1000, 3))

    yield

    # Every step bounded and named (dms_common.shutdown, P91-S6): one step
    # that never completes once hung this service's test teardown for 42 min.
    poll_task.cancel()
    await bounded_shutdown_step("poll_task", poll_task)
    if registration:
        await bounded_shutdown_step("registration.stop", registration.stop())
    sensor_config_proxy.unbind()
    await bounded_shutdown_step("sensor_config_client.stop", app.state.sensor_config_client.stop())
    await bounded_shutdown_step("event_bus.close", event_bus.close())
    for name in ("storage", "virus_scan", "documents", "cases", "permission_client"):
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


async def publish_event(
    event_type: str,
    subject: str,
    payload: dict,
    *,
    event_bus: NatsEventBusClient | None = None,
    actor: str | None = None,
) -> None:
    """`event_bus` defaults to the long-lived `app.state.event_bus` (every
    caller except `_ingest_message` - all reached via a real HTTP request,
    always on the same event loop lifespan started on). `_ingest_message`
    passes its own short-lived, freshly connected instance instead - see its
    own docstring for why."""
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
        actor=actor,
    )
    bus = event_bus if event_bus is not None else app.state.event_bus
    await bus.publish(event_type, event.to_bytes())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


def _require_poststelle(x_dms_principal: str, x_dms_roles: str) -> None:
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="X-DMS-Principal fehlt")
    if not _has_poststelle_role(x_dms_roles):
        raise HTTPException(
            status_code=403,
            detail=f"Nur die Rolle {settings.poststelle_role!r} darf den Posteingang/Postausgang "
            "einsehen/bearbeiten",
        )


# Nested sessions (P94-S3): a request handler passes its own session - a
# second `session_factory()` session while the request's holds a connection
# can deadlock the small pool (2 + 3, ADR 0254), as auth-service's login did.
async def _mailbox_rows(session: AsyncSession | None = None) -> list:
    if session is not None:
        return await mailboxes.list_rows(session)
    async with app.state.session_factory() as own_session:
        return await mailboxes.list_rows(own_session)


async def _mailbox_by_id(mailbox_id: str, session: AsyncSession | None = None):
    if session is not None:
        return await mailboxes.get_row(session, mailbox_id)
    async with app.state.session_factory() as own_session:
        return await mailboxes.get_row(own_session, mailbox_id)


async def _accessible_mailbox_ids(
    principal_id: str, session: AsyncSession | None = None
) -> set[str]:
    """Department RBAC (P32-S3, ADR 0132) - a central mailbox is open to
    every `poststelle_role` holder (`kind="central"` forbids
    `owning_group_id` entirely, see `settings.MailboxConfig`); a
    departmental mailbox additionally requires group membership in its
    `owning_group_id`. Used to scope the UNFILTERED listings (`GET
    /inbound`/`GET /routing-log` without an explicit `mailbox_id`) -
    `GET /mailboxes` deliberately stays unfiltered regardless (see its own
    docstring): the routing-target selector needs every mailbox NAME
    visible, central intake routes to departments its own staff aren't
    members of."""
    rows = await _mailbox_rows(session)
    accessible = {mailbox.id for mailbox in rows if mailbox.kind == "central"}
    for mailbox in rows:
        if mailbox.kind != "departmental":
            continue
        if await app.state.permission_client.is_group_member(mailbox.owning_group_id, principal_id):
            accessible.add(mailbox.id)
    return accessible


async def _require_mailbox_access(
    mailbox_id: str, principal_id: str, session: AsyncSession | None = None
) -> None:
    """Gates read/act access to ONE specific mailbox's content (P32-S3,
    ADR 0132) - an unconfigured `mailbox_id` (e.g. a mailbox removed from
    `DMS_MAILBOXES` after messages were already stored under it) is
    deliberately let through unchecked, same lenient behavior this
    endpoint family already had before this session for an unknown ID."""
    mailbox = await _mailbox_by_id(mailbox_id, session)
    if mailbox is None or mailbox.kind == "central":
        return
    if await app.state.permission_client.is_group_member(mailbox.owning_group_id, principal_id):
        return
    raise HTTPException(
        status_code=403,
        detail=f"Keine Mitgliedschaft in der für Postfach {mailbox_id!r} zuständigen Gruppe",
    )


async def _to_message_out(session: AsyncSession, message) -> InboundMessageOut:
    attachments = await repository.list_attachments(session, message.id)
    routing_log = await repository.list_routing_log(session, message.id)
    base = InboundMessageOut.model_validate(message)
    return base.model_copy(
        update={
            "attachments": [InboundAttachmentOut.model_validate(a) for a in attachments],
            "routing_log": [MailRoutingLogEntryOut.model_validate(r) for r in routing_log],
        }
    )


@app.get("/mailboxes", response_model=list[MailboxOut])
async def list_mailboxes(
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
) -> list[MailboxOut]:
    """The configured mailbox list (14.2, Post-Roadmap Phase 31 Session
    12a) - basis for a mailbox selector in the frontend. Credential fields
    are deliberately excluded (`MailboxOut`), same gate as `/inbound`.
    Deliberately still UNFILTERED by department (P32-S3, ADR 0132) - unlike
    `GET /inbound`/`GET /routing-log`, which DO now scope their CONTENT to
    accessible mailboxes: knowing a mailbox's NAME/`kind` exists is not
    sensitive, and the routing-target selector needs every mailbox visible
    so central intake can route to a department its own staff aren't
    members of (`POST /inbound/{id}/route` only requires access to the
    message's current mailbox, not the target - see there)."""
    _require_poststelle(x_dms_principal, x_dms_roles)
    return [
        MailboxOut(
            id=row.id,
            name=row.name,
            kind=row.kind,
            owning_group_id=row.owning_group_id,
            enabled=row.enabled,
        )
        for row in await _mailbox_rows()
    ]


@app.get("/inbound", response_model=list[InboundMessageOut])
async def list_inbound(
    status_filter: str | None = None,
    mailbox_id: str | None = None,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[InboundMessageOut]:
    """Since P32-S3 (ADR 0132): an explicit `mailbox_id` requires access to
    that one mailbox (`403` otherwise); without one, results are scoped to
    every mailbox the caller can access (central + departments they belong
    to), not silently to everything."""
    _require_poststelle(x_dms_principal, x_dms_roles)
    if mailbox_id is not None:
        await _require_mailbox_access(mailbox_id, x_dms_principal, session)
        messages = await repository.list_messages(
            session, status=status_filter, mailbox_id=mailbox_id
        )
    else:
        accessible = await _accessible_mailbox_ids(x_dms_principal, session)
        messages = await repository.list_messages(
            session, status=status_filter, mailbox_ids=accessible
        )
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
    await _require_mailbox_access(message.mailbox_id, x_dms_principal, session)
    return await _to_message_out(session, message)


@app.post("/inbound/{message_id}/route", response_model=InboundMessageOut)
async def route_message(
    message_id: str,
    payload: RouteMessageRequest,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> InboundMessageOut:
    """Hands a message off to a different mailbox (14.2, Post-Roadmap Phase
    31 Session 12b, ADR 0124) - e.g. central intake routing a message to
    the right department's mailbox. Orthogonal to
    matching/assignment: `status`/`match_*`/`proposed_target_*` are left
    untouched, only `mailbox_id` changes plus a permanent
    `MailRoutingLogEntry` hop record (`GET /inbound/{id}`'s `routing_log`).
    Since P32-S3 (ADR 0132): requires access to the message's CURRENT
    (source) mailbox only - the target mailbox stays deliberately
    unrestricted, so central intake can still route to any department
    without its staff needing membership in every department's group
    (the same "any configured mailbox may route to any other" topology
    this endpoint already had, now scoped by who may act on the source)."""
    _require_poststelle(x_dms_principal, x_dms_roles)
    try:
        message = await repository.get_message(session, message_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_mailbox_access(message.mailbox_id, x_dms_principal, session)
    if message.status in ("confirmed", "rejected"):
        raise HTTPException(
            status_code=409,
            detail=f"Nachricht ist bereits abschließend bearbeitet (status={message.status!r}) "
            "und kann nicht mehr weitergeleitet werden",
        )
    if await _mailbox_by_id(payload.target_mailbox_id, session) is None:
        raise HTTPException(
            status_code=422, detail=f"Postfach {payload.target_mailbox_id!r} ist nicht konfiguriert"
        )
    if payload.target_mailbox_id == message.mailbox_id:
        raise HTTPException(
            status_code=422,
            detail=f"Nachricht befindet sich bereits im Postfach {payload.target_mailbox_id!r}",
        )
    previous_mailbox_id = message.mailbox_id
    try:
        message = await repository.route_message(
            session,
            message_id,
            target_mailbox_id=payload.target_mailbox_id,
            routed_by=x_dms_principal,
            reason=payload.reason,
        )
    except repository.DuplicateInTargetMailboxError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await session.commit()
    await publish_event(
        "mail_connector.message.routed",
        subject=message_id,
        payload={
            "from_mailbox_id": previous_mailbox_id,
            "to_mailbox_id": payload.target_mailbox_id,
            "routed_by": x_dms_principal,
        },
    )
    return await _to_message_out(session, message)


@app.get("/routing-log", response_model=list[MailRoutingLogEntryWithMessageOut])
async def search_routing_log(
    mailbox_id: str | None = None,
    routed_by: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    q: str | None = None,
    limit: int = 200,
    x_dms_principal: str = Header(default=""),
    x_dms_roles: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[MailRoutingLogEntryWithMessageOut]:
    """The searchable, standalone cross-message "Postbuch" register (14.2,
    Post-Roadmap Phase 31 Session 12b built the per-message `routing_log`
    this reads from; this session, 12c, adds the register view across every
    message). `mailbox_id` matches a hop touching that mailbox on either
    side (routed FROM or TO it); `q` is a substring match against the
    message's subject. Since P32-S3 (ADR 0132): an explicit `mailbox_id`
    requires access to that one mailbox (`403` otherwise); without one,
    results are scoped to hops touching at least one mailbox the caller can
    access - a departmental principal no longer sees other departments'
    routing history by default, closing the gap ADR 0123/0124
    "Consequences" left open."""
    _require_poststelle(x_dms_principal, x_dms_roles)
    if mailbox_id is not None:
        await _require_mailbox_access(mailbox_id, x_dms_principal, session)
        rows = await repository.search_routing_log(
            session,
            mailbox_id=mailbox_id,
            routed_by=routed_by,
            since=since,
            until=until,
            q=q,
            limit=limit,
        )
    else:
        accessible = await _accessible_mailbox_ids(x_dms_principal, session)
        rows = await repository.search_routing_log(
            session,
            mailbox_ids=accessible,
            routed_by=routed_by,
            since=since,
            until=until,
            q=q,
            limit=limit,
        )
    return [
        MailRoutingLogEntryWithMessageOut(
            id=entry.id,
            message_id=entry.message_id,
            from_mailbox_id=entry.from_mailbox_id,
            to_mailbox_id=entry.to_mailbox_id,
            routed_by=entry.routed_by,
            routed_at=entry.routed_at,
            reason=entry.reason,
            message_subject=message.subject,
            message_from_address=message.from_address,
            message_current_mailbox_id=message.mailbox_id,
            message_status=message.status,
        )
        for entry, message in rows
    ]


async def _create_documents_for_message(
    session: AsyncSession, message, *, folder_id: str | None, created_by: str, title: str
) -> None:
    """`title` applies to the FIRST document created (deterministically the
    body text, see `_ingest_message`'s `parts.insert(0, ...)`) - with
    multiple attachments, the remaining ones keep their own filename as the
    title, since a single form-provided title does not sensibly fit
    multiple different enclosures at once.

    Detached signatures (P92-S4, ADR 0248): a `.p7s` paired with the file it
    signs (`signature_pairing`) is filed after that file, as derived from it,
    so signature-service validates the pair and shows the result on the
    file."""
    attachments = list(await repository.list_attachments(session, message.id))
    pairs = pair_detached_signatures([a.filename for a in attachments], skip={0})
    # Files first, then the signatures that refer to them.
    order = [i for i in range(len(attachments)) if i not in pairs] + list(pairs)
    for index in order:
        attachment = attachments[index]
        if attachment.scan_status != "clean" or attachment.storage_object_key is None:
            continue
        signs = None
        if index in pairs:
            # The target's document id: filed in this call or an earlier one.
            signs = attachments[pairs[index]].resulting_document_id
        try:
            data = await app.state.storage.download(attachment.storage_object_key)
        except ObjectNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail=f"Zwischengelagerter Anhang {attachment.filename!r} nicht (mehr) vorhanden",
            ) from exc
        try:
            document = await app.state.documents.create_document(
                data=data,
                filename=attachment.filename,
                content_type=attachment.content_type,
                title=title if index == 0 else attachment.filename,
                created_by=created_by,
                folder_id=folder_id,
                signs_document_id=signs,
            )
        except StructureRefusedError as exc:
            # Filing structure (P91-S2a, ADR 0255): keep what was already
            # filed (a retry skips assigned attachments) and name the refused
            # one, so the Poststelle can choose another folder for the rest.
            await session.commit()
            raise HTTPException(
                status_code=422,
                detail={
                    "errors": [f"Anhang „{attachment.filename}“: {error}" for error in exc.errors]
                },
            ) from exc
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
    """Confirms a proposed match (2.5, P15-S3) - not a silent assignment:
    the match was only PROPOSED during retrieval (`status="proposed_match"`),
    only this call actually creates documents."""
    _require_poststelle(x_dms_principal, x_dms_roles)
    try:
        message = await repository.get_message(session, message_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_mailbox_access(message.mailbox_id, x_dms_principal, session)
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
            # Coincides with the folder of the found document (obvious
            # default: a reply concerning a known document is filed next to
            # it) - `None` if that document itself resides at the top
            # level, in which case the new mail ends up there too.
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
    """Manual assignment (2.5) - for messages without (or with ambiguous)
    automatic match, but also usable as a deliberate override of a proposed
    match (e.g. wrong reference number detected)."""
    _require_poststelle(x_dms_principal, x_dms_roles)
    try:
        message = await repository.get_message(session, message_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await _require_mailbox_access(message.mailbox_id, x_dms_principal, session)
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
    await _require_mailbox_access(message.mailbox_id, x_dms_principal, session)
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


async def _attach_related_document(message: EmailMessage, document_id: str) -> None:
    """Attaches the current content of a document already residing in the
    DMS to an outbound message (2.5) - the attachment-side counterpart to
    `_create_documents_for_message`'s inbound path (there: attachment ->
    new document; here: existing document -> attachment). An unknown
    `related_document_id` is a caller input error (400, see call site in
    `send_outbound`), content missing in the storage service (e.g. already
    disposed of) is a 404 - same distinction as
    `_create_documents_for_message`."""
    version = await app.state.documents.get_current_version(document_id)
    if version is None:
        raise HTTPException(
            status_code=400, detail=f"related_document_id {document_id!r} unbekannt"
        )
    try:
        data = await app.state.storage.download(version["storage_object_key"])
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=f"Inhalt des verknüpften Dokuments {document_id!r} nicht (mehr) im "
            "Storage Service vorhanden",
        ) from exc
    content_type = version["content_type"] or "application/octet-stream"
    maintype, _, subtype = content_type.partition("/")
    if not subtype:
        maintype, subtype = "application", "octet-stream"
    message.add_attachment(data, maintype=maintype, subtype=subtype, filename=version["filename"])


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

    # Attachment from a document already residing in the DMS (2.5) -
    # resolved BEFORE the actual send attempt, not wrapped in its
    # try/except: an unknown `related_document_id` is a caller input error
    # (400), not a send-failure situation like an unreachable SMTP server -
    # same principle as `assign_manually`'s upfront check of an unknown
    # `case_id`.
    if payload.related_document_id is not None:
        await _attach_related_document(message, payload.related_document_id)

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


# --- Mailbox administration (P88-S4a, ADR 0244) ----------------------------


async def _require_mail_config(x_dms_principal: str) -> None:
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="X-DMS-Principal fehlt")
    if not await app.state.permission_client.has_permission(
        x_dms_principal, settings.mail_config_permission
    ):
        raise HTTPException(
            status_code=403,
            detail="Fehlende Domain-Admin-Rolle 'Postfach-Konfiguration' (admin.mail_config)",
        )


async def _config_out(session: AsyncSession, row) -> MailboxConfigOut:
    status_entry = app.state.poll_status.get(row.id, {})
    return MailboxConfigOut(
        **mailboxes.row_values(row),
        has_password=row.password_encrypted is not None,
        message_count=await mailboxes.message_count(session, row.id),
        updated_at=row.updated_at,
        updated_by=row.updated_by,
        last_poll_at=status_entry.get("at"),
        last_poll_ok=status_entry.get("ok"),
        last_poll_error=status_entry.get("error"),
    )


def _values_from_payload(payload: MailboxConfigIn) -> dict:
    return {
        "id": payload.id,
        "name": payload.name,
        "kind": payload.kind,
        "owning_group_id": payload.owning_group_id or None,
        "inbound_protocol": payload.inbound_protocol,
        "host": payload.host,
        "port": payload.effective_port(),
        "username": payload.username,
        "use_tls": payload.use_tls,
        "imap_mailbox": payload.imap_mailbox or "INBOX",
        "enabled": payload.enabled,
    }


def _mailbox_error(exc: Exception) -> HTTPException:
    if isinstance(exc, mailboxes.MailboxNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, mailboxes.MailboxConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, mailboxes.MailboxInvalidError):
        return HTTPException(status_code=422, detail=str(exc))
    if isinstance(exc, crypto.CredentialKeyError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, crypto.DecryptionError):
        return HTTPException(
            status_code=409,
            detail=f"{exc} - Passwort neu eingeben (Schlüssel geändert?)",
        )
    raise exc


_MAILBOX_ERRORS = (
    mailboxes.MailboxNotFoundError,
    mailboxes.MailboxConflictError,
    mailboxes.MailboxInvalidError,
    crypto.CredentialKeyError,
    crypto.DecryptionError,
)


@app.get("/mailbox-config", response_model=list[MailboxConfigOut])
async def list_mailbox_config(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[MailboxConfigOut]:
    """Every mailbox with its connection settings (never the password),
    message count and this replica's last poll result."""
    await _require_mail_config(x_dms_principal)
    return [await _config_out(session, row) for row in await mailboxes.list_rows(session)]


@app.post("/mailbox-config", response_model=MailboxConfigOut, status_code=201)
async def create_mailbox_config(
    payload: MailboxConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> MailboxConfigOut:
    await _require_mail_config(x_dms_principal)
    try:
        row = await mailboxes.save(
            session,
            _values_from_payload(payload),
            password=payload.password,
            key=app.state.credentials_key,
            actor=x_dms_principal,
            create=True,
        )
    except _MAILBOX_ERRORS as exc:
        raise _mailbox_error(exc) from exc
    await session.commit()
    await publish_event(
        "mail_connector.mailbox.created",
        subject=row.id,
        payload={"kind": row.kind, "inbound_protocol": row.inbound_protocol, "host": row.host},
        actor=x_dms_principal,
    )
    return await _config_out(session, row)


@app.put("/mailbox-config/{mailbox_id}", response_model=MailboxConfigOut)
async def update_mailbox_config(
    mailbox_id: str,
    payload: MailboxConfigIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> MailboxConfigOut:
    """Replaces every non-secret field; the password only when one is
    given. Takes effect on the next poll tick."""
    await _require_mail_config(x_dms_principal)
    if payload.id != mailbox_id:
        raise HTTPException(status_code=422, detail="Die id eines Postfachs ist unveränderlich")
    try:
        row = await mailboxes.save(
            session,
            _values_from_payload(payload),
            password=payload.password,
            key=app.state.credentials_key,
            actor=x_dms_principal,
            create=False,
        )
    except _MAILBOX_ERRORS as exc:
        raise _mailbox_error(exc) from exc
    await session.commit()
    await publish_event(
        "mail_connector.mailbox.updated",
        subject=row.id,
        payload={"enabled": row.enabled, "password_changed": bool(payload.password)},
        actor=x_dms_principal,
    )
    return await _config_out(session, row)


@app.delete("/mailbox-config/{mailbox_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mailbox_config(
    mailbox_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Only a mailbox without stored messages; otherwise `409` - disable it
    instead, so its messages keep a name and a mailbox to route from."""
    await _require_mail_config(x_dms_principal)
    try:
        await mailboxes.delete(session, mailbox_id)
    except _MAILBOX_ERRORS as exc:
        raise _mailbox_error(exc) from exc
    await session.commit()
    app.state.poll_status.pop(mailbox_id, None)
    await publish_event(
        "mail_connector.mailbox.deleted", subject=mailbox_id, payload={}, actor=x_dms_principal
    )


@app.post("/mailbox-config/test", response_model=MailboxTestResult)
async def test_mailbox_config(
    payload: MailboxTestIn,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> MailboxTestResult:
    """Logs in with a draft configuration, reading no message. Without a
    password in the draft, the stored one of the same id is used - so an
    admin can test a change of host without retyping the secret."""
    await _require_mail_config(x_dms_principal)
    try:
        row = await mailboxes.get_row(session, payload.id)
        password = mailboxes.resolve_password(row, payload.password, app.state.credentials_key)
        config = mailboxes.build_config(_values_from_payload(payload), password)
    except _MAILBOX_ERRORS as exc:
        raise _mailbox_error(exc) from exc
    backend = app.state.backend_factory(config)
    try:
        async with asyncio.timeout(settings.mailbox_test_timeout_seconds):
            await backend.check()
    except TimeoutError:
        return MailboxTestResult(ok=False, error="Zeitüberschreitung beim Verbindungsaufbau")
    except Exception as exc:  # noqa: BLE001 - every failure is a test result
        return MailboxTestResult(ok=False, error=f"{type(exc).__name__}: {exc}"[:500])
    return MailboxTestResult(ok=True)
