from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ReportType = Literal["document_volume", "open_workflow_tasks", "storage_usage", "user_activity"]
ReportFormat = Literal["csv", "pdf"]
Frequency = Literal["daily", "weekly", "monthly"]
GroupBy = Literal["day", "week", "month"]


class DocumentVolumeEntry(BaseModel):
    period: str
    folder_id: str | None
    count: int


class OpenWorkflowTaskEntry(BaseModel):
    instance_id: str
    process_definition_id: str
    business_key: str | None
    task_id: str
    task_name: str
    lane: str | None


class StorageUsageEntry(BaseModel):
    backend: str
    object_count: int
    total_size_bytes: int


class UserActivityEntry(BaseModel):
    actor: str
    event_type: str
    count: int


class ReportScheduleCreate(BaseModel):
    report_type: ReportType
    format: ReportFormat
    frequency: Frequency
    recipient_email: str
    filters: dict = {}


class ReportScheduleOut(BaseModel):
    id: str
    report_type: ReportType
    format: ReportFormat
    frequency: Frequency
    recipient_email: str
    filters: dict
    next_run_at: datetime
    last_run_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReportRunOut(BaseModel):
    id: str
    schedule_id: str | None
    report_type: ReportType
    format: ReportFormat
    generated_at: datetime

    model_config = {"from_attributes": True}


TraceCategory = Literal["view", "download", "change", "delete"]


class ForensicTraceEntry(BaseModel):
    """Ein Ereignis im Forensik-Trace (5.4b, seit P7-S2c) - Rohform aus
    audit-service, angereichert um die aus dem `event_type`-Suffix
    abgeleitete `category` (siehe `forensic.categorize_event_type`)."""

    id: int
    event_type: str
    category: TraceCategory
    occurred_at: datetime
    service_name: str
    subject: str | None
    actor: str | None
    payload: dict


class ForensicTraceResult(BaseModel):
    entries: list[ForensicTraceEntry]
    anomalies: list[str]
