from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class QueryResult(BaseModel):
    """Rohe Ereignis-Dicts statt eines eigenen, dupliziertes Schemas von
    `audit-service`s `AuditEventOut` - query-service ist ein reiner
    Durchreicher, keine eigene Datenhaltung (siehe ADR-0031-Folgeentscheidung
    "keine eigene Datenhaltung" in PROGRESS.md). `total_before_filter` macht
    die RBAC-/Bereichssperren-Filterung (6.1) fuer die aufrufende Person
    transparent nachvollziehbar, statt Zeilen stillschweigend verschwinden
    zu lassen."""

    events: list[dict]
    total_before_filter: int
    total_after_filter: int
    superuser: bool


class QueryTextRequest(BaseModel):
    query_text: str


class ManipulationModeActivateRequest(BaseModel):
    duration_minutes: int


class ManipulationModeStatusOut(BaseModel):
    active: bool
    activated_by: str | None
    expires_at: datetime | None


class DryRunRequest(BaseModel):
    action_type: str
    params: dict


class DryRunResult(BaseModel):
    action_type: str
    preview: str
    is_critical: bool
    dry_run_token: str


class ManipulateExecuteRequest(BaseModel):
    dry_run_token: str


class ManipulateExecuteResult(BaseModel):
    status: Literal["executed", "pending_approval"]
    result: dict | None = None
    approval_request_id: str | None = None
