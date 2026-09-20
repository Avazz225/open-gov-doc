import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, field_validator

# Deliberately a pragmatic format check, not exhaustive RFC 5322 validation -
# same "good enough to catch a typo, not a full grammar" scope this project
# already accepts elsewhere (e.g. federation-hub-service's own version-string
# validator). No dependency on `pydantic[email]`/`email-validator` - neither
# is used anywhere else in this repo (every other `email: str` field across
# auth-service/notification-service accepts a plain, unvalidated string), so
# adding one here would be a new, isolated convention rather than reused
# established practice.
_EMAIL_FORMAT_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

ReportType = Literal[
    "document_volume",
    "open_workflow_tasks",
    "storage_usage",
    "user_activity",
    "license_utilization",
]
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


class LicenseUtilizationEntry(BaseModel):
    """One row per license dimension (5.4a, Phase 45 Session 1) - flattened
    from `license-service`'s single `GET /license/status` snapshot
    (`documents`/`storage_gb`/`users`, each a `limit`/`current`/`exceeded`
    triple) into the same one-row-per-entry shape every other report in
    this service already uses. `dimension="license"` with every other
    field `None`/`False` is the one exception, used only when no license
    is installed at all."""

    dimension: str
    limit: float | int | None
    current: float | int | None
    exceeded: bool


class ReportScheduleCreate(BaseModel):
    report_type: ReportType
    format: ReportFormat
    frequency: Frequency
    recipient_email: str
    filters: dict = {}

    @field_validator("recipient_email")
    @classmethod
    def _validate_recipient_email_format(cls, value: str) -> str:
        """Phase 53 Session 3 finding: without this check, a typo'd address
        was accepted without complaint here and only ever surfaced at the
        next poll tick, inside `notification_client.send_email` - logged,
        not surfaced anywhere a person would see it (see `_run_due_
        schedules`'s new per-schedule status tracking for the OTHER half of
        this fix: a syntactically valid but genuinely undeliverable address,
        which this format check cannot catch)."""
        if not _EMAIL_FORMAT_PATTERN.match(value):
            raise ValueError(f"{value!r} sieht nicht wie eine gueltige E-Mail-Adresse aus")
        return value


class ReportScheduleOut(BaseModel):
    id: str
    report_type: ReportType
    format: ReportFormat
    frequency: Frequency
    recipient_email: str
    filters: dict
    next_run_at: datetime
    last_run_at: datetime | None
    # Visible failure status (Phase 53 Session 3) - `last_status` is `None`
    # until the schedule has ever actually run once (matches `last_run_at`'s
    # own "None until first run" semantics), then `"sent"`/`"failed"`.
    last_status: Literal["sent", "failed"] | None
    last_error: str | None
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
    # Row-level RBAC filtering (5.4b, Post-Roadmap Phase 36 Session 3) - same
    # transparency fields as query-service's own `QueryResult`: `entries`
    # only ever contains what the caller is allowed to see, but the UI
    # still needs to know how much was hidden (or that nothing was, for the
    # activated superuser).
    total_before_filter: int = 0
    total_after_filter: int = 0
    superuser: bool = False
