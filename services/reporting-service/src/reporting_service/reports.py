import csv
import io
from collections import Counter
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from sqlalchemy.ext.asyncio import AsyncSession

from reporting_service import repository
from reporting_service.clients import (
    AuditClient,
    LicenseServiceClient,
    StorageClient,
    WorkflowClient,
)
from reporting_service.schemas import (
    DocumentVolumeEntry,
    LicenseUtilizationEntry,
    OpenWorkflowTaskEntry,
    StorageUsageEntry,
    UserActivityEntry,
)


async def document_volume(
    session: AsyncSession,
    *,
    since: datetime | None,
    until: datetime | None,
    folder_id: str | None,
    group_by: str,
) -> list[DocumentVolumeEntry]:
    rows = await repository.get_document_volume(
        session, since=since, until=until, folder_id=folder_id, group_by=group_by
    )
    return [
        DocumentVolumeEntry(period=period, folder_id=fid, count=count)
        for period, fid, count in rows
    ]


async def open_workflow_tasks(workflow_client: WorkflowClient) -> list[OpenWorkflowTaskEntry]:
    entries: list[OpenWorkflowTaskEntry] = []
    for instance in await workflow_client.list_active_instances():
        tasks = await workflow_client.list_tasks(instance["id"])
        for task in tasks:
            entries.append(
                OpenWorkflowTaskEntry(
                    instance_id=instance["id"],
                    process_definition_id=str(instance["process_definition_id"]),
                    business_key=instance.get("business_key"),
                    task_id=task["id"],
                    task_name=task["name"],
                    lane=task.get("lane"),
                )
            )
    return entries


async def storage_usage(storage_client: StorageClient) -> list[StorageUsageEntry]:
    return [StorageUsageEntry(**entry) for entry in await storage_client.get_usage()]


async def license_utilization(
    license_client: LicenseServiceClient,
) -> list[LicenseUtilizationEntry]:
    """License utilization report (5.4a, Phase 45 Session 1) - deferred since
    the original standard-reports session (P7-S2b) purely because
    license-service didn't exist yet (Phase 9). `GET /license/status`
    already returns a live-computed snapshot of all three dimensions
    together (`documents`/`storage_gb`/`users`) - this just flattens that
    single object into one row per dimension, the same shape every other
    report in this module already produces, so `to_csv`/`to_pdf` need no
    changes. No license installed at all (`installed: false`) - none of
    the three dimensions are ever populated in that case (see
    `license-service`'s own `_status_out`) - reported as a single row
    naming that state instead of three empty/misleading dimension rows."""
    status = await license_client.get_status()
    if not status.get("installed"):
        return [
            LicenseUtilizationEntry(dimension="license", limit=None, current=None, exceeded=False)
        ]
    entries = []
    for dimension in ("documents", "storage_gb", "users"):
        usage = status.get(dimension)
        if usage is None:
            continue
        entries.append(
            LicenseUtilizationEntry(
                dimension=dimension,
                limit=usage.get("limit"),
                current=usage.get("current"),
                exceeded=usage.get("exceeded", False),
            )
        )
    return entries


async def user_activity(
    audit_client: AuditClient,
    *,
    principal_id: str,
    actor: str | None,
    since: datetime | None,
    until: datetime | None,
) -> list[UserActivityEntry]:
    """Aggregiert die rohen Audit-Events client-seitig nach Akteur+Aktions-
    typ - audit-service selbst liefert nur die Rohliste (P7-S2-Filter-API),
    keine Aggregation; das ist bewusst reporting-services eigene Aufgabe."""
    events = await audit_client.list_events(
        principal_id=principal_id, actor=actor, since=since, until=until
    )
    counts: Counter[tuple[str, str]] = Counter()
    for event in events:
        event_actor = event.get("actor")
        if event_actor is None:
            continue
        counts[(event_actor, event["event_type"])] += 1
    return [
        UserActivityEntry(actor=a, event_type=t, count=c)
        for (a, t), c in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def to_csv(headers: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def to_pdf(title: str, headers: list[str], rows: list[list[str]]) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, title=title)
    table_data = [headers, *rows]
    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#333333")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ]
        )
    )
    doc.build([table])
    return buffer.getvalue()
