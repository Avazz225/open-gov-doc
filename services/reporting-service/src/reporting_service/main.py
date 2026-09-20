import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

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

from reporting_service import forensic, reports, repository
from reporting_service.clients import (
    AuditClient,
    AuthServiceClient,
    LicenseServiceClient,
    NotificationClient,
    StorageClient,
    WorkflowClient,
)
from reporting_service.consumer import start_consuming
from reporting_service.filtering import filter_entries_by_permission
from reporting_service.models import Base
from reporting_service.schemas import (
    DocumentVolumeEntry,
    ForensicTraceEntry,
    ForensicTraceResult,
    GroupBy,
    LicenseUtilizationEntry,
    OpenWorkflowTaskEntry,
    ReportFormat,
    ReportScheduleCreate,
    ReportScheduleOut,
    StorageUsageEntry,
    TraceCategory,
    UserActivityEntry,
)
from reporting_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)

# Post-Roadmap Phase 38 Session 2: fixed principal for the report-schedule
# poll loop's own audit-service calls - `ReportSchedule` has no
# creator/initiator field to attribute a background tick to a real human,
# and `audit.read` being granted to "everyone" means any principal string
# works regardless of whether it identifies a real registered account.
_SCHEDULED_REPORT_PRINCIPAL = "reporting-service-scheduler"


async def _run_due_schedules(session_factory) -> None:
    """A single poll tick, extracted from `_report_schedule_poll_loop` for
    direct testability (no infinite loop/no `sleep` here). Generates the
    report when due, uploads it to the Storage Service and notifies via
    email with a download link (no attachment, see
    docs/services/reporting-service.md)."""
    async with session_factory() as session:
        due = await repository.list_due_schedules(session, now=datetime.now(UTC))
        for schedule in due:
            try:
                content, content_type = await _generate_report(
                    session,
                    schedule.report_type,
                    schedule.format,
                    schedule.filters,
                    # `ReportSchedule` has no creator/initiator field to
                    # attribute this background tick to a real human (5.4a,
                    # unchanged since P7-S2b) - a fixed system principal is
                    # safe here since `audit.read` is granted to "everyone"
                    # (Post-Roadmap Phase 38 Session 2), which applies
                    # regardless of whether the principal string identifies
                    # a real registered account.
                    principal_id=_SCHEDULED_REPORT_PRINCIPAL,
                )
            except Exception as exc:
                logger.exception(
                    "Berichtsgenerierung fuer Planung %r fehlgeschlagen - "
                    "naechster Versuch beim naechsten faelligen Zeitpunkt.",
                    schedule.id,
                )
                await repository.mark_schedule_run(
                    session,
                    schedule,
                    ran_at=datetime.now(UTC),
                    status="failed",
                    error=f"Berichtsgenerierung fehlgeschlagen: {exc}",
                )
                await session.commit()
                continue

            run_id = str(uuid.uuid4())
            key = f"reports/{schedule.id}/{run_id}.{schedule.format}"
            await app.state.storage_client.upload(key, content, content_type)
            run = await repository.create_report_run(
                session,
                schedule_id=schedule.id,
                report_type=schedule.report_type,
                format=schedule.format,
                storage_object_key=key,
                content_type=content_type,
            )
            download_url = (
                f"{settings.gateway_base_url}/api/reporting-service/report-runs/{run.id}/download"
            )
            # Own try/except (Phase 53 Session 3) - previously NOT
            # per-schedule-isolated: a bad/no-longer-deliverable address
            # (notification-service rejects any `recipient` that isn't a
            # real registered account's email, not just a malformed string,
            # see `_EMAIL_FORMAT_PATTERN` above for the format-only half of
            # this fix) raised out of this whole function, aborting the
            # ENTIRE tick - every other due schedule after this one in the
            # same `due` list was silently skipped too, and `mark_schedule_
            # run` was never called for the failing schedule, so it never
            # advanced past "due" and retried forever with zero visible
            # record of ever having failed.
            try:
                await app.state.notification_client.send_email(
                    recipient=schedule.recipient_email,
                    subject=f"DMS-Bericht: {schedule.report_type}",
                    body=(
                        f"Der geplante Bericht {schedule.report_type!r} steht bereit. "
                        f"Download: {download_url}"
                    ),
                )
            except Exception as exc:
                logger.exception(
                    "E-Mail-Versand fuer Planung %r fehlgeschlagen - "
                    "naechster Versuch beim naechsten faelligen Zeitpunkt.",
                    schedule.id,
                )
                await repository.mark_schedule_run(
                    session,
                    schedule,
                    ran_at=datetime.now(UTC),
                    status="failed",
                    error=f"E-Mail-Versand fehlgeschlagen: {exc}",
                )
                await session.commit()
                continue

            await repository.mark_schedule_run(
                session, schedule, ran_at=datetime.now(UTC), status="sent"
            )
            await session.commit()


async def _report_schedule_poll_loop(session_factory) -> None:
    """Due-date poll for scheduled reports (5.4a) - same idiom as
    document-service's `_retention_poll_loop`/workflow-service's `_sla_poll_
    loop`. Since Post-Roadmap Phase 44 Session 3 (4.8, ADR 0164): skips
    the whole tick while maintenance mode is active - same rationale as
    the other poll loops ADR 0152 named, applied here too - and, same
    fix every other rollout site needed, the check itself lives inside
    the existing `try`, not before it."""
    while True:
        try:
            if await app.state.permission_client.is_maintenance_active():
                await asyncio.sleep(settings.report_poll_interval_seconds)
                continue
            await _run_due_schedules(session_factory)
        except Exception:
            logger.exception(
                "Bericht-Planungs-Poll-Tick fehlgeschlagen - wird beim naechsten Tick "
                "erneut versucht."
            )
        await asyncio.sleep(settings.report_poll_interval_seconds)


async def _generate_report(
    session: AsyncSession, report_type: str, format: str, filters: dict, *, principal_id: str
) -> tuple[bytes, str]:
    if report_type == "document_volume":
        entries = await reports.document_volume(
            session,
            since=_parse_dt(filters.get("since")),
            until=_parse_dt(filters.get("until")),
            folder_id=filters.get("folder_id"),
            group_by=filters.get("group_by", "day"),
        )
        headers = ["period", "folder_id", "count"]
        rows = [[e.period, e.folder_id or "", str(e.count)] for e in entries]
    elif report_type == "open_workflow_tasks":
        entries = await reports.open_workflow_tasks(app.state.workflow_client)
        headers = [
            "instance_id",
            "process_definition_id",
            "business_key",
            "task_id",
            "task_name",
            "lane",
        ]
        rows = [
            [
                e.instance_id,
                e.process_definition_id,
                e.business_key or "",
                e.task_id,
                e.task_name,
                e.lane or "",
            ]
            for e in entries
        ]
    elif report_type == "storage_usage":
        entries = await reports.storage_usage(app.state.storage_client)
        headers = ["backend", "object_count", "total_size_bytes"]
        rows = [[e.backend, str(e.object_count), str(e.total_size_bytes)] for e in entries]
    elif report_type == "license_utilization":
        entries = await reports.license_utilization(app.state.license_client)
        headers = ["dimension", "limit", "current", "exceeded"]
        rows = [
            [
                e.dimension,
                "" if e.limit is None else str(e.limit),
                "" if e.current is None else str(e.current),
                str(e.exceeded),
            ]
            for e in entries
        ]
    elif report_type == "user_activity":
        entries = await reports.user_activity(
            app.state.audit_client,
            principal_id=principal_id,
            actor=filters.get("actor"),
            since=_parse_dt(filters.get("since")),
            until=_parse_dt(filters.get("until")),
        )
        headers = ["actor", "event_type", "count"]
        rows = [[e.actor, e.event_type, str(e.count)] for e in entries]
    else:
        raise ValueError(f"Unbekannter Berichtstyp {report_type!r}")

    if format == "csv":
        return reports.to_csv(headers, rows), "text/csv"
    return reports.to_pdf(report_type, headers, rows), "application/pdf"


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


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


async def _fetch_forensic_trace(
    *,
    principal_id: str,
    is_superuser: bool,
    actor: str | None,
    subject: str | None,
    event_type: str | None,
    category: str | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
) -> tuple[list[ForensicTraceEntry], list[str], int]:
    """Forensic trace (5.4b, since P7-S2c): fetches the raw event list via
    audit-service's P7-S2 filter API, categorizes it client-side
    (audit-service itself has no concept of "category"). Since Post-Roadmap
    Phase 36 Session 3, additionally applies row-level RBAC filtering
    (`filtering.filter_entries_by_permission`, parity with query-service's
    own `filtering.py`) BEFORE anomaly detection runs - an anomaly computed
    over events the caller isn't allowed to see would itself be an
    information leak. Returns the post-filter entries, anomalies computed
    only over those, and the pre-filter count (for the "N of M visible"
    transparency the UI shows, same pattern as query-service's own
    `QueryResult`)."""
    raw_events = await app.state.audit_client.list_events(
        principal_id=principal_id,
        actor=actor,
        subject=subject,
        event_type=event_type,
        since=since,
        until=until,
        limit=limit,
    )
    entries: list[ForensicTraceEntry] = []
    for raw in raw_events:
        entry_category = forensic.categorize_event_type(raw["event_type"])
        if category is not None and entry_category != category:
            continue
        entries.append(
            ForensicTraceEntry(
                id=raw["id"],
                event_type=raw["event_type"],
                category=entry_category,
                occurred_at=datetime.fromisoformat(raw["occurred_at"]),
                service_name=raw["service_name"],
                subject=raw.get("subject"),
                actor=raw.get("actor"),
                payload=raw.get("payload") or {},
            )
        )
    total_before_filter = len(entries)
    entries = await filter_entries_by_permission(
        entries,
        principal_id=principal_id,
        permission_client=app.state.permission_client,
        is_superuser=is_superuser,
    )
    anomalies = forensic.detect_download_anomalies(
        [
            {"event_type": e.event_type, "actor": e.actor, "occurred_at": e.occurred_at}
            for e in entries
        ],
        threshold_count=settings.anomaly_download_threshold_count,
        threshold_minutes=settings.anomaly_download_threshold_minutes,
    )
    return entries, anomalies, total_before_filter


async def _record_trace_query(
    *,
    queried_by: str,
    actor: str | None,
    subject: str | None,
    event_type: str | None,
    category: str | None,
    since: datetime | None,
    until: datetime | None,
) -> None:
    """Self-auditing of trace access (5.4b, literal concept requirement:
    "itself audited again as an access") - unconditional, cannot be
    disabled, since this is itself the control mechanism."""
    await publish_event(
        "reporting.forensic_trace.queried",
        subject,
        {
            "actor": actor,
            "subject": subject,
            "event_type": event_type,
            "category": category,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        actor=queried_by,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS reporting"))
        await conn.run_sync(Base.metadata.create_all)
        # Ad-hoc schema extension (no Alembic, see CONTRIBUTING.md):
        # `last_status`/`last_error` only added in Phase 53 Session 3,
        # `create_all` only creates missing tables, not missing columns on
        # existing ones - same established pattern as e.g. permission-
        # service's own `ADD COLUMN IF NOT EXISTS` migrations.
        await conn.execute(
            text(
                "ALTER TABLE reporting.report_schedule "
                "ADD COLUMN IF NOT EXISTS last_status VARCHAR(16)"
            )
        )
        await conn.execute(
            text("ALTER TABLE reporting.report_schedule ADD COLUMN IF NOT EXISTS last_error TEXT")
        )
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.workflow_client = WorkflowClient(settings.workflow_service_base_url)
    app.state.audit_client = AuditClient(settings.audit_service_base_url)
    app.state.storage_client = StorageClient(settings.storage_service_base_url)
    app.state.notification_client = NotificationClient(settings.notification_service_base_url)
    app.state.permission_client = PermissionServiceClient(settings.permission_service_base_url)
    app.state.auth_client = AuthServiceClient(settings.auth_service_base_url)
    app.state.license_client = LicenseServiceClient(settings.license_service_base_url)

    sensor_config_client = SensorConfigClient(settings.monitoring_service_base_url)
    await sensor_config_client.start()
    sensor_config_proxy.bind(sensor_config_client)
    app.state.sensor_config_client = sensor_config_client
    app.state.sensor_registry = sensor_registry

    # Own producer bus (5.4b, since P7-S2c) - for self-auditing forensic
    # trace access ("who queried which trace and when"). Same dual-bus
    # pattern as document-service: consumer_bus (below) remains for
    # document.>, event_bus is new and independent.
    event_bus = NatsEventBusClient(settings.nats_url, stream="reporting")
    await event_bus.connect()
    app.state.event_bus = event_bus

    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    await start_consuming(consumer_bus, settings.subjects, app.state.session_factory)

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
        sensors=http_sensor_declarations(),
    )

    poll_task = asyncio.create_task(_report_schedule_poll_loop(app.state.session_factory))

    startup_end = time.time()
    millis = round((startup_end - startup_start) * 1000, 3)
    logger.info("Startup completed in %s ms.", millis, exc_info=True)

    yield

    poll_task.cancel()
    with suppress(asyncio.CancelledError):
        await poll_task
    if registration:
        await registration.stop()
    sensor_config_proxy.unbind()
    await app.state.sensor_config_client.stop()
    await consumer_bus.close()
    await event_bus.close()
    await app.state.workflow_client.close()
    await app.state.audit_client.close()
    await app.state.storage_client.close()
    await app.state.notification_client.close()
    await app.state.license_client.close()
    await app.state.permission_client.close()
    await app.state.auth_client.close()
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


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/metrics")
def get_metrics() -> Response:
    body, content_type = metrics_payload(app.state.sensor_registry)
    return Response(content=body, media_type=content_type)


async def _require_reporting_permission(
    x_dms_principal: str, *, permission: str, access_type: str
) -> None:
    """RBAC (Post-Roadmap Phase 19 Session 7, ADR 0072) - reporting-service
    previously had NO permission check at all, not even for the forensic
    trace despite its heightened sensitivity. Checks against the root
    resource (`root`) - reporting-service does not register its own
    resource tree nodes. Standard reports/schedules use
    `reporting.read`/`reporting.write`, the forensic trace the separate,
    narrower `reporting.forensic_trace` (its own permission instead of
    `reporting.read`, since it potentially exposes sensitive user activity,
    see docs/services/reporting-service.md "Open Points"). The "everyone"
    group (ADR 0067) grants all three by default - preserves the previous
    de-facto-open behavior while making it admin-editable."""
    if not x_dms_principal:
        raise HTTPException(status_code=401, detail="Fehlender X-DMS-Principal-Header")
    allowed = await app.state.permission_client.check(
        principal_id=x_dms_principal,
        resource_id=PermissionServiceClient.ROOT_RESOURCE_ID,
        permission=permission,
        access_type=access_type,
    )
    if not allowed:
        raise HTTPException(status_code=403, detail=f"Fehlende Berechtigung {permission!r}")


async def _is_active_superuser(x_dms_principal: str) -> bool:
    """Row-level RBAC filtering for the forensic trace (5.4b, Post-Roadmap
    Phase 36 Session 3) - 1:1 pattern from `query-service`/`permission-
    service`. The activated superuser (4.6) is exempt from the row-level
    filter below (same concept-6.1 exception query-service's own structured
    queries already grant), not from `_require_reporting_permission`'s
    outer capability gate, which is unaffected."""
    active, superuser_principal_id = await app.state.auth_client.get_active_superuser()
    return active and bool(x_dms_principal) and superuser_principal_id == x_dms_principal


@app.get("/reports/document-volume", response_model=list[DocumentVolumeEntry])
async def get_document_volume_report(
    since: datetime | None = None,
    until: datetime | None = None,
    folder_id: str | None = None,
    group_by: GroupBy = "day",
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[DocumentVolumeEntry]:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    return await reports.document_volume(
        session, since=since, until=until, folder_id=folder_id, group_by=group_by
    )


@app.get("/reports/document-volume/export")
async def export_document_volume_report(
    format: ReportFormat,
    since: datetime | None = None,
    until: datetime | None = None,
    folder_id: str | None = None,
    group_by: GroupBy = "day",
    x_dms_principal: str = Header(default=""),
) -> Response:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    filters = {
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "folder_id": folder_id,
        "group_by": group_by,
    }
    async with app.state.session_factory() as session:
        content, content_type = await _generate_report(
            session, "document_volume", format, filters, principal_id=x_dms_principal
        )
    return Response(content=content, media_type=content_type)


@app.get("/reports/open-workflow-tasks", response_model=list[OpenWorkflowTaskEntry])
async def get_open_workflow_tasks_report(
    x_dms_principal: str = Header(default=""),
) -> list[OpenWorkflowTaskEntry]:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    return await reports.open_workflow_tasks(app.state.workflow_client)


@app.get("/reports/open-workflow-tasks/export")
async def export_open_workflow_tasks_report(
    format: ReportFormat, x_dms_principal: str = Header(default="")
) -> Response:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    async with app.state.session_factory() as session:
        content, content_type = await _generate_report(
            session, "open_workflow_tasks", format, {}, principal_id=x_dms_principal
        )
    return Response(content=content, media_type=content_type)


@app.get("/reports/storage-usage", response_model=list[StorageUsageEntry])
async def get_storage_usage_report(
    x_dms_principal: str = Header(default=""),
) -> list[StorageUsageEntry]:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    return await reports.storage_usage(app.state.storage_client)


@app.get("/reports/storage-usage/export")
async def export_storage_usage_report(
    format: ReportFormat, x_dms_principal: str = Header(default="")
) -> Response:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    async with app.state.session_factory() as session:
        content, content_type = await _generate_report(
            session, "storage_usage", format, {}, principal_id=x_dms_principal
        )
    return Response(content=content, media_type=content_type)


@app.get("/reports/license-utilization", response_model=list[LicenseUtilizationEntry])
async def get_license_utilization_report(
    x_dms_principal: str = Header(default=""),
) -> list[LicenseUtilizationEntry]:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    return await reports.license_utilization(app.state.license_client)


@app.get("/reports/license-utilization/export")
async def export_license_utilization_report(
    format: ReportFormat, x_dms_principal: str = Header(default="")
) -> Response:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    async with app.state.session_factory() as session:
        content, content_type = await _generate_report(
            session, "license_utilization", format, {}, principal_id=x_dms_principal
        )
    return Response(content=content, media_type=content_type)


@app.get("/reports/user-activity", response_model=list[UserActivityEntry])
async def get_user_activity_report(
    actor: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    x_dms_principal: str = Header(default=""),
) -> list[UserActivityEntry]:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    return await reports.user_activity(
        app.state.audit_client,
        principal_id=x_dms_principal,
        actor=actor,
        since=since,
        until=until,
    )


@app.get("/reports/user-activity/export")
async def export_user_activity_report(
    format: ReportFormat,
    actor: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    x_dms_principal: str = Header(default=""),
) -> Response:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    filters = {
        "actor": actor,
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
    }
    async with app.state.session_factory() as session:
        content, content_type = await _generate_report(
            session, "user_activity", format, filters, principal_id=x_dms_principal
        )
    return Response(content=content, media_type=content_type)


@app.post(
    "/report-schedules", response_model=ReportScheduleOut, status_code=status.HTTP_201_CREATED
)
async def create_report_schedule(
    payload: ReportScheduleCreate,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> ReportScheduleOut:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.write", access_type="write"
    )
    schedule = await repository.create_schedule(
        session,
        report_type=payload.report_type,
        format=payload.format,
        frequency=payload.frequency,
        recipient_email=payload.recipient_email,
        filters=payload.filters,
    )
    await session.commit()
    return schedule


@app.get("/report-schedules", response_model=list[ReportScheduleOut])
async def list_report_schedules(
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> list[ReportScheduleOut]:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    return await repository.list_schedules(session)


@app.delete("/report-schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report_schedule(
    schedule_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.write", access_type="write"
    )
    try:
        await repository.delete_schedule(session, schedule_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


@app.get("/report-runs/{run_id}/download")
async def download_report_run(
    run_id: str,
    x_dms_principal: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.read", access_type="read"
    )
    try:
        run = await repository.get_report_run(session, run_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    data = await app.state.storage_client.download(run.storage_object_key)
    return Response(content=data, media_type=run.content_type)


@app.get("/forensic-trace", response_model=ForensicTraceResult)
async def get_forensic_trace(
    actor: str | None = None,
    subject: str | None = None,
    event_type: str | None = None,
    category: TraceCategory | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 5000,
    x_dms_principal: str = Header(default=""),
) -> ForensicTraceResult:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.forensic_trace", access_type="read"
    )
    is_superuser = await _is_active_superuser(x_dms_principal)
    entries, anomalies, total_before_filter = await _fetch_forensic_trace(
        principal_id=x_dms_principal,
        is_superuser=is_superuser,
        actor=actor,
        subject=subject,
        event_type=event_type,
        category=category,
        since=since,
        until=until,
        limit=limit,
    )
    await _record_trace_query(
        queried_by=x_dms_principal,
        actor=actor,
        subject=subject,
        event_type=event_type,
        category=category,
        since=since,
        until=until,
    )
    return ForensicTraceResult(
        entries=entries,
        anomalies=anomalies,
        total_before_filter=total_before_filter,
        total_after_filter=len(entries),
        superuser=is_superuser,
    )


@app.get("/forensic-trace/export")
async def export_forensic_trace(
    format: ReportFormat,
    actor: str | None = None,
    subject: str | None = None,
    event_type: str | None = None,
    category: TraceCategory | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 5000,
    x_dms_principal: str = Header(default=""),
) -> Response:
    await _require_reporting_permission(
        x_dms_principal, permission="reporting.forensic_trace", access_type="read"
    )
    is_superuser = await _is_active_superuser(x_dms_principal)
    entries, _, _ = await _fetch_forensic_trace(
        principal_id=x_dms_principal,
        is_superuser=is_superuser,
        actor=actor,
        subject=subject,
        event_type=event_type,
        category=category,
        since=since,
        until=until,
        limit=limit,
    )
    await _record_trace_query(
        queried_by=x_dms_principal,
        actor=actor,
        subject=subject,
        event_type=event_type,
        category=category,
        since=since,
        until=until,
    )
    headers = [
        "occurred_at",
        "event_type",
        "category",
        "service_name",
        "subject",
        "actor",
        "payload",
    ]
    rows = [
        [
            e.occurred_at.isoformat(),
            e.event_type,
            e.category,
            e.service_name,
            e.subject or "",
            e.actor or "",
            json.dumps(e.payload, ensure_ascii=False),
        ]
        for e in entries
    ]
    if format == "csv":
        return Response(content=reports.to_csv(headers, rows), media_type="text/csv")
    return Response(
        content=reports.to_pdf("forensic_trace", headers, rows), media_type="application/pdf"
    )
