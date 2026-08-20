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
    deleted_by: str | None
    retention_until: datetime | None
    full_deletion: bool
    pending_deletion_reason: str | None
    reminder_notify_email: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# --- Retention/legal hold/forced deletion for folders (5.2/5.2a, since P7-S1b) ---


class TrashRequest(BaseModel):
    deleted_by: str


class TrashResult(BaseModel):
    """Deletion request workflow for regular users (5.2, since P7-S1c) -
    two possible outcomes, same wrapper pattern as
    `document_service.ForceReleaseResult`/`TrashResult`: executed
    immediately, or deferred via the four-eyes principle (action type
    `folder.delete`, independent of the already-existing
    retention-triggered `folder.force_delete`)."""

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
    trigger: Literal["forced_deletion", "trash_expiry", "manual_purge"]
    reason: str | None
    triggered_by: str | None
    occurred_at: datetime

    model_config = {"from_attributes": True}


class ReconcileRestoreDeletionRequest(BaseModel):
    """Deletion reconciliation after restore (10.4, P11-S4) - structurally
    identical to
    `document_service.schemas.ReconcileRestoreDeletionRequest`."""

    original_entry_id: str
    reason: str | None = None


class RetentionConfigIn(BaseModel):
    deletion_reason_required: bool = False
    reminder_lead_days: int | None = None
    deletion_reason_catalog: list[str] = []


class RetentionConfigOut(RetentionConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime


class TrashConfigIn(BaseModel):
    restore_period_days: int = 30


class TrashConfigOut(TrashConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime


# --- Structure templates (2.5/7.3, since P15-S6) ---


class FolderTemplateNode(BaseModel):
    """A node in the nested structure tree of a template - deliberately no
    `attributes` field, a skeleton captures only name + object type."""

    name: str
    object_type_id: int | None = None
    children: list["FolderTemplateNode"] = []


FolderTemplateNode.model_rebuild()


class FolderTemplateCreate(BaseModel):
    source_folder_id: str
    name: str
    description: str | None = None
    created_by: str


class FolderTemplateOut(BaseModel):
    id: str
    name: str
    description: str | None
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class FolderTemplateDetailOut(FolderTemplateOut):
    structure: FolderTemplateNode


class FolderTemplateApplyRequest(BaseModel):
    target_parent_id: str
    created_by: str


class FolderTemplateApplyResult(BaseModel):
    root_folder: FolderOut
    created_count: int
