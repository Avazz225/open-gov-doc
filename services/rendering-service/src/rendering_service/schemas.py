from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ExportHistoryEntryIn(BaseModel):
    """One row of a document's export history (Phase 28, ADR 0107) - the
    caller (document-service) already resolved this from audit-service's
    `document.exported` event log; rendering-service only renders it, it
    never queries audit-service itself."""

    happened_at: datetime
    actor: str | None = None
    action: str


class RenditionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    document_id: str
    version_number: int
    rendition_type: str
    source_filename: str
    source_content_type: str | None
    target_filename: str
    target_content_type: str
    size_bytes: int
    status: Literal["ready", "failed", "failed_permanent"]
    error_message: str | None
    attempts: int
    next_retry_at: datetime | None
    created_at: datetime
    updated_at: datetime
