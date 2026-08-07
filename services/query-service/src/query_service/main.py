import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime

from dms_common import configure_logging
from dms_eventbus_client import Event, NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import FastAPI, Header, HTTPException, status

from query_service.clients import (
    AuditClient,
    AuthServiceClient,
    DocumentClient,
    PermissionServiceClient,
)
from query_service.filtering import filter_events_by_permission
from query_service.parser import ParserPluginError, load_parser_plugin
from query_service.schemas import QueryResult, QueryTextRequest
from query_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


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
    """Konzept 6.1: "ohne Authentifizierung ist keine einzige Abfrage
    möglich" - der aktivierte Superuser (4.6) ist die einzige Ausnahme, sonst
    ist die Domain-Admin-Rolle "Query-Konsole" (`admin.query_console`)
    zwingend, gleiches Gate-Muster wie `workflow-service._require_object_
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


async def _record_query(
    *, principal_id: str, source: str, params: dict, total_before: int, total_after: int
) -> None:
    """Selbst-Auditierung jeder Abfrage (Konzept 6.1 Punkt 5, "Vollständige
    Protokollierung") - unconditional, kein Abschalten möglich, gleiches
    Muster wie reporting-service's `_record_trace_query` (5.4b)."""
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

    app.state.audit_client = AuditClient(settings.audit_service_base_url)
    app.state.document_client = DocumentClient(settings.document_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.auth_client = AuthServiceClient(settings.auth_service_base_url)

    # Reiner Producer-Bus fuer die Selbst-Auditierung (Punkt 5, 6.1) - kein
    # Consumer, query-service haelt kein eigenes Read-Modell (siehe ADR 0031
    # Folgeentscheidung "keine eigene Datenhaltung").
    event_bus = NatsEventBusClient(settings.nats_url, stream="query")
    await event_bus.connect()
    app.state.event_bus = event_bus

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
    await event_bus.close()
    await app.state.audit_client.close()
    await app.state.document_client.close()
    await app.state.permission_client.close()
    await app.state.auth_client.close()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


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
