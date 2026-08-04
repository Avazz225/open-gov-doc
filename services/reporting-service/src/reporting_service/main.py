import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from dms_common import configure_logging
from dms_db_base import build_engine, make_session_factory
from dms_eventbus_client import NatsEventBusClient
from dms_registry_client import maybe_start_registration
from fastapi import Depends, FastAPI, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from reporting_service import reports, repository
from reporting_service.clients import AuditClient, NotificationClient, StorageClient, WorkflowClient
from reporting_service.consumer import start_consuming
from reporting_service.models import Base
from reporting_service.schemas import (
    DocumentVolumeEntry,
    GroupBy,
    OpenWorkflowTaskEntry,
    ReportFormat,
    ReportScheduleCreate,
    ReportScheduleOut,
    StorageUsageEntry,
    UserActivityEntry,
)
from reporting_service.settings import Settings

settings = Settings()
configure_logging(settings)
logger = logging.getLogger(__name__)


async def _run_due_schedules(session_factory) -> None:
    """Ein einzelner Poll-Tick, ausgelagert aus `_report_schedule_poll_loop`
    fuer direkte Testbarkeit (keine Endlosschleife/kein `sleep` hier). Erzeugt
    bei Faelligkeit den Bericht, laedt ihn im Storage Service ab und
    benachrichtigt per E-Mail mit einem Downloadlink (kein Anhang, siehe
    docs/services/reporting-service.md)."""
    async with session_factory() as session:
        due = await repository.list_due_schedules(session, now=datetime.now(UTC))
        for schedule in due:
            try:
                content, content_type = await _generate_report(
                    session, schedule.report_type, schedule.format, schedule.filters
                )
            except Exception:
                logger.exception(
                    "Berichtsgenerierung fuer Planung %r fehlgeschlagen - "
                    "naechster Versuch beim naechsten faelligen Zeitpunkt.",
                    schedule.id,
                )
                await repository.mark_schedule_run(session, schedule, ran_at=datetime.now(UTC))
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
            await app.state.notification_client.send_email(
                recipient=schedule.recipient_email,
                subject=f"DMS-Bericht: {schedule.report_type}",
                body=(
                    f"Der geplante Bericht {schedule.report_type!r} steht bereit. "
                    f"Download: {download_url}"
                ),
            )
            await repository.mark_schedule_run(session, schedule, ran_at=datetime.now(UTC))
            await session.commit()


async def _report_schedule_poll_loop(session_factory) -> None:
    """Faelligkeits-Poll fuer geplante Berichte (5.4a) - gleiches Idiom wie
    document-service's `_retention_poll_loop`/workflow-service's `_sla_poll_
    loop`."""
    while True:
        try:
            await _run_due_schedules(session_factory)
        except Exception:
            logger.exception(
                "Bericht-Planungs-Poll-Tick fehlgeschlagen - wird beim naechsten Tick "
                "erneut versucht."
            )
        await asyncio.sleep(settings.report_poll_interval_seconds)


async def _generate_report(
    session: AsyncSession, report_type: str, format: str, filters: dict
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
    elif report_type == "user_activity":
        entries = await reports.user_activity(
            app.state.audit_client,
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    startup_start = time.time()
    engine = build_engine(settings.postgres_dsn)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS reporting"))
        await conn.run_sync(Base.metadata.create_all)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)

    app.state.workflow_client = WorkflowClient(settings.workflow_service_base_url)
    app.state.audit_client = AuditClient(settings.audit_service_base_url)
    app.state.storage_client = StorageClient(settings.storage_service_base_url)
    app.state.notification_client = NotificationClient(settings.notification_service_base_url)

    consumer_bus = NatsEventBusClient(settings.nats_url, ensure_stream=False)
    await consumer_bus.connect()
    app.state.consumer_bus = consumer_bus
    await start_consuming(consumer_bus, settings.subjects, app.state.session_factory)

    registration = await maybe_start_registration(
        registry_service_base_url=settings.registry_service_base_url,
        self_address=settings.self_address,
        service_type=settings.service_name,
        version="0.1.0",
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
    await consumer_bus.close()
    await app.state.workflow_client.close()
    await app.state.audit_client.close()
    await app.state.storage_client.close()
    await app.state.notification_client.close()
    await engine.dispose()


app = FastAPI(title=settings.service_name, lifespan=lifespan)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with app.state.session_factory() as session:
        yield session


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok", "service": settings.service_name}


@app.get("/reports/document-volume", response_model=list[DocumentVolumeEntry])
async def get_document_volume_report(
    since: datetime | None = None,
    until: datetime | None = None,
    folder_id: str | None = None,
    group_by: GroupBy = "day",
    session: AsyncSession = Depends(get_session),
) -> list[DocumentVolumeEntry]:
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
) -> Response:
    filters = {
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
        "folder_id": folder_id,
        "group_by": group_by,
    }
    async with app.state.session_factory() as session:
        content, content_type = await _generate_report(session, "document_volume", format, filters)
    return Response(content=content, media_type=content_type)


@app.get("/reports/open-workflow-tasks", response_model=list[OpenWorkflowTaskEntry])
async def get_open_workflow_tasks_report() -> list[OpenWorkflowTaskEntry]:
    return await reports.open_workflow_tasks(app.state.workflow_client)


@app.get("/reports/open-workflow-tasks/export")
async def export_open_workflow_tasks_report(format: ReportFormat) -> Response:
    async with app.state.session_factory() as session:
        content, content_type = await _generate_report(session, "open_workflow_tasks", format, {})
    return Response(content=content, media_type=content_type)


@app.get("/reports/storage-usage", response_model=list[StorageUsageEntry])
async def get_storage_usage_report() -> list[StorageUsageEntry]:
    return await reports.storage_usage(app.state.storage_client)


@app.get("/reports/storage-usage/export")
async def export_storage_usage_report(format: ReportFormat) -> Response:
    async with app.state.session_factory() as session:
        content, content_type = await _generate_report(session, "storage_usage", format, {})
    return Response(content=content, media_type=content_type)


@app.get("/reports/user-activity", response_model=list[UserActivityEntry])
async def get_user_activity_report(
    actor: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[UserActivityEntry]:
    return await reports.user_activity(
        app.state.audit_client, actor=actor, since=since, until=until
    )


@app.get("/reports/user-activity/export")
async def export_user_activity_report(
    format: ReportFormat,
    actor: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Response:
    filters = {
        "actor": actor,
        "since": since.isoformat() if since else None,
        "until": until.isoformat() if until else None,
    }
    async with app.state.session_factory() as session:
        content, content_type = await _generate_report(session, "user_activity", format, filters)
    return Response(content=content, media_type=content_type)


@app.post(
    "/report-schedules", response_model=ReportScheduleOut, status_code=status.HTTP_201_CREATED
)
async def create_report_schedule(
    payload: ReportScheduleCreate, session: AsyncSession = Depends(get_session)
) -> ReportScheduleOut:
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
    session: AsyncSession = Depends(get_session),
) -> list[ReportScheduleOut]:
    return await repository.list_schedules(session)


@app.delete("/report-schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_report_schedule(
    schedule_id: str, session: AsyncSession = Depends(get_session)
) -> None:
    try:
        await repository.delete_schedule(session, schedule_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await session.commit()


@app.get("/report-runs/{run_id}/download")
async def download_report_run(
    run_id: str, session: AsyncSession = Depends(get_session)
) -> Response:
    try:
        run = await repository.get_report_run(session, run_id)
    except repository.NotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    data = await app.state.storage_client.download(run.storage_object_key)
    return Response(content=data, media_type=run.content_type)
