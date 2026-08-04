from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class FolderCreate(BaseModel):
    name: str
    parent_id: str = "root"
    object_type_id: int | None = None
    attributes: dict = {}
    created_by: str


class FolderUpdate(BaseModel):
    name: str | None = None
    parent_id: str | None = None
    attributes: dict | None = None


class FolderOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    object_type_id: int | None
    attributes: dict
    deleted_at: datetime | None
    retention_until: datetime | None
    full_deletion: bool
    pending_deletion_reason: str | None
    reminder_notify_email: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Aufbewahrung/Legal Hold/Zwangslöschung für Ordner (5.2/5.2a, seit P7-S1b) ---


class TrashRequest(BaseModel):
    deleted_by: str


class TrashResult(BaseModel):
    """Löschantrag-Workflow für reguläre Nutzer (5.2, seit P7-S1c) - zwei
    mögliche Ausgänge, gleiches Wrapper-Muster wie `document_service.
    ForceReleaseResult`/`TrashResult`: sofort ausgeführt, oder per
    Vier-Augen-Prinzip (Aktionstyp `folder.delete`, unabhängig von der
    bereits bestehenden retentionsgetriggerten `folder.force_delete`)
    zurückgestellt."""

    status: Literal["trashed", "pending_approval"]
    folder: FolderOut | None = None
    approval_request_id: str | None = None


class RetentionUpdate(BaseModel):
    retention_until: datetime | None = None
    full_deletion: bool = False
    reason: str | None = None
    notify_email: str | None = None


class LegalHoldCreate(BaseModel):
    folder_id: str
    set_by: str
    reason: str | None = None


class LegalHoldReleaseRequest(BaseModel):
    released_by: str


class LegalHoldOut(BaseModel):
    id: str
    folder_id: str
    reason: str | None
    set_by: str
    set_at: datetime
    released_by: str | None
    released_at: datetime | None

    model_config = {"from_attributes": True}


class DeletionRegisterEntryOut(BaseModel):
    id: str
    folder_id: str
    trigger: Literal["forced_deletion", "trash_expiry"]
    reason: str | None
    triggered_by: str | None
    occurred_at: datetime

    model_config = {"from_attributes": True}


class RetentionConfigIn(BaseModel):
    deletion_reason_required: bool = False
    reminder_lead_days: int | None = None


class RetentionConfigOut(RetentionConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime


class TrashConfigIn(BaseModel):
    restore_period_days: int = 30


class TrashConfigOut(TrashConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime
