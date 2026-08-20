from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DocumentUpdate(BaseModel):
    title: str | None = None
    attributes: dict | None = None
    # Moving to a different folder (P12-S1, user request for the
    # WebDAV connector: WebDAV MOVE is a mandatory method - without this
    # field there was no server-side counterpart for documents, even though
    # folder-service has already supported the same thing for folders since
    # P3-S3 via `parent_id`).
    folder_id: str | None = None


class DocumentOut(BaseModel):
    id: str
    title: str
    folder_id: str | None
    object_type_id: int | None
    attributes: dict
    current_version_number: int
    deleted_at: datetime | None
    deleted_by: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    derived_from_document_id: str | None
    derived_from_version_number: int | None
    originating_case_id: str | None
    retention_until: datetime | None
    full_deletion: bool
    pending_deletion_reason: str | None
    reminder_notify_email: str | None
    archive_after: datetime | None
    archived_at: datetime | None
    archive_format: str | None
    dehydrated_at: datetime | None
    registered_at: datetime | None
    classification_level: str | None

    model_config = {"from_attributes": True}


class DocumentVersionOut(BaseModel):
    version_number: int
    filename: str
    content_type: str | None
    size_bytes: int
    checksum_sha256: str
    is_conflict: bool
    based_on_version_number: int | None
    comment: str | None
    created_by: str
    created_at: datetime
    # Internal storage key (5.6, since P7-S3) - `archival-service`
    # needs it to address exactly the same live object identity when
    # dehydrating/retrieving as the regular upload/download path.
    storage_object_key: str
    # Snapshot at check-in time (post-roadmap phase 31 session 3, ADR 0114).
    classification_level: str | None

    model_config = {"from_attributes": True}


class CheckinResult(BaseModel):
    version: DocumentVersionOut
    is_conflict: bool


class LockOut(BaseModel):
    document_id: str
    locked_by: str
    session_id: str
    based_on_version_number: int
    locked_at: datetime
    expires_at: datetime

    model_config = {"from_attributes": True}


class LockAcquireRequest(BaseModel):
    locked_by: str
    session_id: str
    timeout_seconds: float | None = None


class LockReleaseRequest(BaseModel):
    released_by: str


class LockForceReleaseRequest(BaseModel):
    released_by: str
    reason: str | None = None


class ForceReleaseResult(BaseModel):
    """Two possible outcomes (4.3, P6-S4): executed immediately, or
    deferred under the four-eyes principle (see `approval_client.py`) - same
    wrapper pattern as `CheckinResult`."""

    status: Literal["released", "pending_approval"]
    lock: LockOut | None = None
    approval_request_id: str | None = None


class TrashRequest(BaseModel):
    deleted_by: str


class TrashResult(BaseModel):
    """Deletion request workflow for regular users (5.2, since
    P7-S1c) - same two-outcome wrapper pattern as `ForceReleaseResult`:
    executed immediately, or deferred under the four-eyes principle (action
    type `document.delete`, independent of the already existing
    retention-triggered `document.force_delete`)."""

    status: Literal["trashed", "pending_approval"]
    document: DocumentOut | None = None
    approval_request_id: str | None = None


class UploadConfigIn(BaseModel):
    # Empty = no restriction (default, see models.UploadConfig).
    allowed_content_types: list[str] = Field(default_factory=list)


class UploadConfigOut(UploadConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime


class AuditTraceConfigIn(BaseModel):
    log_viewed: bool = True
    log_downloaded: bool = True


class AuditTraceConfigOut(AuditTraceConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime


class AuditTraceRoleOverrideIn(BaseModel):
    # None = base applies for this category, otherwise an explicit override.
    log_viewed: bool | None = None
    log_downloaded: bool | None = None


class AuditTraceRoleOverrideOut(AuditTraceRoleOverrideIn):
    model_config = {"from_attributes": True}

    role: str
    updated_at: datetime


class RetentionUpdate(BaseModel):
    """Set retention/forced deletion (5.2/5.2a, since P7-S1). `reason`
    is checked server-side against the `RetentionConfig`/`ObjectType`
    override (required/optional) - see main.py._resolve_deletion_reason_required."""

    retention_until: datetime | None = None
    full_deletion: bool = False
    reason: str | None = None
    notify_email: str | None = None


class LegalHoldCreate(BaseModel):
    document_id: str
    set_by: str
    reason: str | None = None


class LegalHoldReleaseRequest(BaseModel):
    released_by: str


class LegalHoldOut(BaseModel):
    id: str
    document_id: str
    reason: str | None
    set_by: str
    set_at: datetime
    released_by: str | None
    released_at: datetime | None

    model_config = {"from_attributes": True}


class ClassificationLevelUpdate(BaseModel):
    """Set/raise a document's classification level (post-roadmap phase 31
    session 3, ADR 0114) - `changed_by` mirrors `DocumentRegisterRequest.
    registered_by` below (opaque, attribution/audit only, independent of the
    `X-DMS-Principal`-based `admin.classification` permission check)."""

    classification_level: Literal["VS-NfD", "VS-VERTRAULICH", "GEHEIM", "STRENG GEHEIM"]
    changed_by: str


class DocumentRegisterRequest(BaseModel):
    """Draft -> registered transition (post-roadmap phase 31 session 2,
    ADR 0113): `registered_by` is opaque, same pattern as `deleted_by`/
    `released_by` elsewhere in this service - no `X-DMS-Principal`
    enforcement for this endpoint, consistent with `create_document`/
    `update_document`."""

    registered_by: str


class DeletionRegisterEntryOut(BaseModel):
    id: str
    document_id: str
    trigger: Literal["forced_deletion", "trash_expiry", "manual_purge"]
    reason: str | None
    triggered_by: str | None
    occurred_at: datetime

    model_config = {"from_attributes": True}


class ReconcileRestoreDeletionRequest(BaseModel):
    """Deletion reconciliation after restore (10.4, P11-S4) -
    `original_entry_id` refers to the deletion register ledger entry (see
    audit-service) whose physical forced deletion was undone by a restore
    and is now being executed again."""

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


class CascadeTrashRequest(BaseModel):
    """Internal service-to-service call from `folder-service` (5.2,
    since P7-S1b) - see main.py `POST /documents/cascade-trash`."""

    folder_ids: list[str]
    via_folder_id: str
    deleted_by: str


class CascadeRestoreRequest(BaseModel):
    via_folder_id: str


class CascadeResult(BaseModel):
    document_ids: list[str]


class CountActiveRequest(BaseModel):
    folder_ids: list[str]


class CountActiveResult(BaseModel):
    count: int


class MarkArchivedRequest(BaseModel):
    """Callback from `archival-service` once the archive copy has
    been verified (5.6, since P7-S3) - `archive_format` is `"pdf_a"`
    (LibreOffice PDF/A export filter or existing `pypdf` tagging logic)."""

    archive_format: str


class ArchiveStatusOut(BaseModel):
    document_id: str
    archive_after: datetime | None
    archived_at: datetime | None
    archive_format: str | None
    dehydrated_at: datetime | None


class HasActiveHoldOut(BaseModel):
    has_active_hold: bool


# --- Public share link (4.2a, P14-S10) ------------------------------


class ShareLinkConfigIn(BaseModel):
    enabled: bool = True
    max_validity_days: int = 30


class ShareLinkConfigOut(ShareLinkConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime


class ShareLinkCreate(BaseModel):
    expires_at: datetime


class ShareLinkOut(BaseModel):
    model_config = {"from_attributes": True}

    token: str
    document_id: str
    created_by: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    revoked_by: str | None


class PublicShareLinkOut(BaseModel):
    """Deliberately ONLY the minimum needed for display (concept's
    literal wording) - no `attributes`/`folder_id`/`created_by` or similar,
    which would reveal something about the document or its context beyond a
    plain preview/download."""

    title: str
    content_type: str | None
    size_bytes: int
    expires_at: datetime


class WebdavEditTokenOut(BaseModel):
    """Direct Office editing (post-roadmap feature) - deliberately
    does NOT return `principal_id` to the client (the client already knows
    its own identity via `X-DMS-Principal`, the token itself is the only
    information `webdav-connector` needs later)."""

    token: str
    expires_at: datetime


class WebdavEditTokenSummary(BaseModel):
    """For the list of active/past tokens (`GET
    .../webdav-edit-tokens`) - unlike `WebdavEditTokenOut`, this includes
    the full picture including `principal_id`, since this endpoint already
    requires an existing write permission (no public/token-based access
    like with share links)."""

    model_config = {"from_attributes": True}

    token: str
    document_id: str
    principal_id: str
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None
    revoked_by: str | None


class WebdavEditTokenResolveOut(BaseModel):
    """Intended only for the internal (east-west, no gateway) call
    from `webdav-connector` - see `GET /internal/webdav-edit-tokens/
    {token}`."""

    document_id: str
    principal_id: str


# --- PDF export with export history & combined folder export
# (post-roadmap phase 28, ADR 0107) ----------------------------------


class ExportConfigIn(BaseModel):
    history_position: Literal["before", "after"] = "after"


class ExportConfigOut(ExportConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime


class FolderExportJobOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    folder_id: str
    history_position: str
    status: Literal["pending", "processing", "completed", "failed_permanent"]
    error_message: str | None
    attempts: int
    next_retry_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime
