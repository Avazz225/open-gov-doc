from datetime import datetime
from typing import Any, Literal

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
    retention_pseudonymize: bool
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
    # Automatic retention-expiry pseudonymization (5.2, Phase 58 Session 1) -
    # mutually exclusive with `full_deletion`, enforced in main.py.put_retention.
    retention_pseudonymize: bool = False
    reason: str | None = None
    notify_email: str | None = None


class PseudonymizeAttributeRequest(BaseModel):
    """Trigger pseudonymization of one personal-data attribute (5.2, Phase
    58 Session 1, mirrors document-service's ADR 0156)."""

    pseudonymized_by: str
    reason: str | None = None


class RevealAttributeRequest(BaseModel):
    revealed_by: str


class PseudonymizedAttributeOut(BaseModel):
    id: str
    folder_id: str
    attribute_name: str
    reason: str | None
    pseudonymized_by: str
    pseudonymized_at: datetime
    last_revealed_by: str | None
    last_revealed_at: datetime | None

    model_config = {"from_attributes": True}


class RevealedAttributeOut(BaseModel):
    folder_id: str
    attribute_name: str
    value: Any
    reason: str | None
    pseudonymized_by: str
    pseudonymized_at: datetime


class FolderDocumentReferenceAdd(BaseModel):
    document_id: str
    added_by: str


class FolderDocumentReferenceRemove(BaseModel):
    removed_by: str


class FolderDocumentReferenceOut(BaseModel):
    """Combines the reference row with a live-resolved snapshot of the
    referenced document (14.2, post-roadmap phase 31 session 7, ADR 0118) -
    same shape/reasoning as case-service's `CaseDocumentReferenceOut`. No
    `from_attributes`, since the resolved fields don't live on the DB model
    itself."""

    document_id: str
    added_by: str
    added_at: datetime
    removed_by: str | None
    removed_at: datetime | None
    current_version_number: int | None
    document_deleted_at: datetime | None


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
