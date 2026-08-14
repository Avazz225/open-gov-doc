import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, Header, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from query_service import consumer, dry_run_tokens, manipulation, manipulation_mode
from query_service.clients import (
    AuditClient,
    AuthServiceClient,
    DocumentClient,
    ObjectTypeClient,
    PermissionServiceClient,
)
from query_service.filtering import filter_events_by_permission
from query_service.models import Base
from query_service.parser import ParserPluginError, load_parser_plugin
from query_service.schemas import (
    DryRunRequest,
    DryRunResult,
    ManipulateExecuteRequest,
    ManipulateExecuteResult,
    ManipulationModeActivateRequest,
    ManipulationModeStatusOut,
    QueryResult,
    QueryTextRequest,
)
from query_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


@dataclass
class _ManipulationClients:
    document_client: DocumentClient
    object_type_client: ObjectTypeClient
    permission_client: PermissionServiceClient


async def publish_event(
    event_type: str, subject: str | None, payload: dict, actor: str | None = None
) -> None:
    event = Event(
        event_type=event_type,
        service_name=settings.service_name,
        subject=subject,
        payload=payload,
        actor=actor,
    )
    await app.state.event_bus.publish(event_type, event.to_bytes())


async def _is_active_superuser(x_dms_principal: str) -> bool:
    active, superuser_principal_id = await app.state.auth_client.get_active_superuser()
    return active and bool(x_dms_principal) and superuser_principal_id == x_dms_principal


async def _require_query_console(x_dms_principal: str, is_superuser: bool) -> None:
    """Concept 6.1: "without authentication, not a single query is
    possible" - the activated superuser (4.6) is the only exception,
    otherwise the domain-admin role "query console" (`admin.query_console`)
    is mandatory, same gate pattern as `workflow-service._require_object_
    config`/`auth-service._require_user_management`."""
    if is_superuser:
        return
    allowed = bool(x_dms_principal) and await app.state.permission_client.has_permission(
        x_dms_principal, settings.query_console_permission
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Query-Konsole'",
        )


async def _require_manipulate_permission(x_dms_principal: str, is_superuser: bool) -> None:
    """Concept 6.1 explicitly names "no manipulation" as a separately
    grantable restriction - its own, fine-grained permission separate from
    the read permission `admin.query_console` above."""
    if is_superuser:
        return
    allowed = bool(x_dms_principal) and await app.state.permission_client.has_permission(
        x_dms_principal, settings.query_console_manipulate_permission
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Fehlende Domain-Admin-Rolle 'Query-Konsole (Manipulation)'",
        )


async def _require_manipulation_mode_active(session: AsyncSession, is_superuser: bool) -> None:
    """Safety switch (concept 6.1 item 1) - the activated superuser (4.6)
    "can read and write without restriction" and does not need to activate
    it separately."""
    if is_superuser:
        return
    mode = await manipulation_mode.get_status(session)
    if not manipulation_mode.is_active(mode):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manipulationsmodus nicht aktiv - siehe POST /manipulation-mode/activate",
        )


def _manipulation_clients() -> _ManipulationClients:
    return _ManipulationClients(
        document_client=app.state.document_client,
        object_type_client=app.state.object_type_client,
        permission_client=app.state.permission_client,
    )


async def _record_query(
    *, principal_id: str, source: str, params: dict, total_before: int, total_after: int
) -> None:
    """Self-auditing of every query (concept 6.1 item 5, "complete
    logging") - unconditional, cannot be turned off, same pattern as
    reporting-service's `_record_trace_query` (5.4b)."""
    await publish_event(
        "query.executed",
        None,
        {
            "source": source,
            "params": params,
            "total_before_filter": total_before,
            "total_after_filter": total_after,
        },
        actor=principal_id,
    )


async def _run_query(
    *,
    principal_id: str,
    is_superuser: bool,
    source: str,
    actor: str | None,
    subject: str | None,
    event_type: str | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> QueryResult:
    events = await app.state.audit_client.list_events(
        actor=actor, subject=subject, event_type=event_type, since=since, until=until, limit=limit
    )
    filtered = await filter_events_by_permission(
        events,
        principal_id=principal_id,
        permission_client=app.state.permission_client,
        document_client=app.state.document_client,
        is_superuser=is_superuser,
    )
    await _record_query(
        principal_id=principal_id,
        source=source,
        params={
            "actor": actor,
            "subject": subject,
            "event_type": event_type,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
            "limit": limit,
        },
        total_before=len(events),
        total_after=len(filtered),
    )
    return QueryResult(
        events=filtered,
        total_before_filter=len(events),
        total_after_filter=len(filtered),
        superuser=is_superuser,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()

    # Genuinely own state (safety switch, since P8-S2) - not a read model of
    # a foreign service, does not reverse the P8-S1 decision "no own data
    # storage" (which was only directed against duplicating foreign read
    # models), see models.py.
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS query"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.audit_client = AuditClient(settings.audit_service_base_url)
    app.state.document_client = DocumentClient(settings.document_service_base_url)
    app.state.object_type_client = ObjectTypeClient(settings.object_type_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.auth_client = AuthServiceClient(settings.auth_service_base_url)

    # Producer bus for self-auditing (item 5, 6.1).
    event_bus = NatsEventBusClient(settings.nats_url, stream="query")
    await event_bus.connect()
    app.state.event_bus = event_bus

    # New consumer bus since P8-S2 (P8-S1 only had the producer above) -
    # executes manipulation actions previously deferred via four-eyes after
    # approval, identical dual-bus pattern as document-service.
    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    await consumer.start_consuming(
        consumer_bus,
        ["permission.approval.approved"],
        _manipulation_clients(),
        publish_event,
    )

    app.state.parser_plugin = load_parser_plugin(settings.query_parser_plugin_module)

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
    )

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis)

    yield

    if registration:
        await registration.stop()
    await consumer_bus.close()
    await event_bus.close()
    await app.state.audit_client.close()
    await app.state.document_client.close()
    await app.state.object_type_client.close()
    await app.state.permission_client.close()
    await app.state.auth_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/query/events", response_model=QueryResult)
async def query_events(
    actor: str | None = None,
    subject: str | None = None,
    event_type: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 100,
    x_dms_principal: str = Header(default=""),
) -> QueryResult:
    is_superuser = await _is_active_superuser(x_dms_principal)
    await _require_query_console(x_dms_principal, is_superuser)
    return await _run_query(
        principal_id=x_dms_principal,
        is_superuser=is_superuser,
        source="structured",
        actor=actor,
        subject=subject,
        event_type=event_type,
        since=since,
        until=until,
        limit=limit,
    )


@app.post("/query", response_model=QueryResult)
async def query_text(
    payload: QueryTextRequest,
    x_dms_principal: str = Header(default=""),
) -> QueryResult:
    is_superuser = await _is_active_superuser(x_dms_principal)
    await _require_query_console(x_dms_principal, is_superuser)

    if app.state.parser_plugin is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=(
                "Kein Query-Parser-Plugin installiert - die strukturierte Filter-API "
                "(GET /query/events) bleibt davon unberuehrt nutzbar. Siehe ADR 0031."
            ),
        )
    try:
        parsed = app.state.parser_plugin.parse(payload.query_text)
    except ParserPluginError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if parsed.table != "events":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unbekannte Tabelle {parsed.table!r} - in dieser Session nur 'events'.",
        )

    filters = parsed.filters
    since_raw = filters.get("since")
    until_raw = filters.get("until")
    return await _run_query(
        principal_id=x_dms_principal,
        is_superuser=is_superuser,
        source="sql",
        actor=filters.get("actor"),
        subject=filters.get("subject"),
        event_type=filters.get("event_type"),
        since=datetime.fromisoformat(since_raw) if since_raw else None,
        until=datetime.fromisoformat(until_raw) if until_raw else None,
        limit=parsed.limit or 100,
    )


@app.post("/manipulation-mode/activate", response_model=ManipulationModeStatusOut)
async def activate_manipulation_mode(
    payload: ManipulationModeActivateRequest,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ManipulationModeStatusOut:
    """Safety switch (concept 6.1 item 1) - "comparable to a break-glass
    access", but its own, lighter-weight mechanism (see
    docs/services/query-service.md for the distinction from 4.6)."""
    is_superuser = await _is_active_superuser(x_dms_principal)
    await _require_manipulate_permission(x_dms_principal, is_superuser)
    mode = await manipulation_mode.activate(
        session, activated_by=x_dms_principal, duration_minutes=payload.duration_minutes
    )
    await session.commit()
    return ManipulationModeStatusOut(
        active=mode.active, activated_by=mode.activated_by, expires_at=mode.expires_at
    )


@app.post("/manipulation-mode/deactivate", response_model=ManipulationModeStatusOut)
async def deactivate_manipulation_mode(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ManipulationModeStatusOut:
    is_superuser = await _is_active_superuser(x_dms_principal)
    await _require_manipulate_permission(x_dms_principal, is_superuser)
    mode = await manipulation_mode.deactivate(session)
    await session.commit()
    return ManipulationModeStatusOut(
        active=mode.active, activated_by=mode.activated_by, expires_at=mode.expires_at
    )


@app.get("/manipulation-mode/status", response_model=ManipulationModeStatusOut)
async def manipulation_mode_status(
    session: AsyncSession = Depends(get_session),
) -> ManipulationModeStatusOut:
    mode = await manipulation_mode.get_status(session)
    return ManipulationModeStatusOut(
        active=manipulation_mode.is_active(mode),
        activated_by=mode.activated_by,
        expires_at=mode.expires_at,
    )


@app.post("/manipulate/dry-run", response_model=DryRunResult)
async def manipulate_dry_run(
    payload: DryRunRequest,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> DryRunResult:
    """Concept 6.1 item 2: "every write query is initially only simulated" -
    mandatory for ALL callers, including the activated superuser (the
    concept names no exception here, only for the safety switch/RBAC)."""
    is_superuser = await _is_active_superuser(x_dms_principal)
    await _require_manipulate_permission(x_dms_principal, is_superuser)
    await _require_manipulation_mode_active(session, is_superuser)

    try:
        action = manipulation.get_action(payload.action_type)
    except manipulation.UnknownActionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        preview = await action.dry_run(payload.params, _manipulation_clients())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    token = dry_run_tokens.issue_token(
        action_type=payload.action_type,
        params=payload.params,
        principal_id=x_dms_principal,
        secret=settings.dry_run_secret,
        ttl_seconds=settings.dry_run_token_ttl_seconds,
    )
    return DryRunResult(
        action_type=payload.action_type,
        preview=preview,
        is_critical=action.is_critical,
        dry_run_token=token,
    )


@app.post("/manipulate/execute", response_model=ManipulateExecuteResult)
async def manipulate_execute(
    payload: ManipulateExecuteRequest,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ManipulateExecuteResult:
    is_superuser = await _is_active_superuser(x_dms_principal)
    await _require_manipulate_permission(x_dms_principal, is_superuser)
    await _require_manipulation_mode_active(session, is_superuser)

    try:
        claims = dry_run_tokens.decode(payload.dry_run_token, secret=settings.dry_run_secret)
    except dry_run_tokens.InvalidDryRunTokenError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    action_type = claims["action_type"]
    params = claims["params"]
    action = manipulation.get_action(action_type)

    # Concept 6.1 item 4: critical actions always enforce four-eyes,
    # independent of the installation-wide configuration - even for the
    # activated superuser (the only place where the superuser may not act
    # "without restriction").
    requires_approval = action.is_critical
    if not requires_approval:
        requires_approval = await app.state.permission_client.requires_approval(action_type)

    if requires_approval:
        request = await app.state.permission_client.create_approval_request(
            action_type=action_type,
            initiated_by=x_dms_principal,
            payload={"params": params, "principal_id": x_dms_principal},
        )
        return ManipulateExecuteResult(status="pending_approval", approval_request_id=request["id"])

    try:
        result = await action.execute(params, _manipulation_clients())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await publish_event(
        "query.manipulation.executed",
        None,
        {"action_type": action_type, "params": params, "result": result},
        actor=x_dms_principal,
    )
    return ManipulateExecuteResult(status="executed", result=result)
