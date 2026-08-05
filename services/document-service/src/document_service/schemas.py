from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DocumentUpdate(BaseModel):
    title: str | None = None
    attributes: dict | None = None


class DocumentOut(BaseModel):
    id: str
    title: str
    folder_id: str | None
    object_type_id: int | None
    attributes: dict
    current_version_number: int
    deleted_at: datetime | None
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
    # Interner Storage-Schlüssel (5.6, seit P7-S3) - `archival-service` braucht
    # ihn, um beim Dehydrieren/Zurückholen exakt dieselbe Live-Objekt-Identität
    # anzusprechen wie der reguläre Upload-/Download-Pfad.
    storage_object_key: str

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
    """Zwei mögliche Ausgänge (4.3, P6-S4): sofort ausgeführt, oder per
    Vier-Augen-Prinzip zurückgestellt (siehe `approval_client.py`) - gleiches
    Wrapper-Muster wie `CheckinResult`."""

    status: Literal["released", "pending_approval"]
    lock: LockOut | None = None
    approval_request_id: str | None = None


class TrashRequest(BaseModel):
    deleted_by: str


class TrashResult(BaseModel):
    """Löschantrag-Workflow für reguläre Nutzer (5.2, seit P7-S1c) - gleiches
    Zwei-Ausgänge-Wrapper-Muster wie `ForceReleaseResult`: sofort ausgeführt,
    oder per Vier-Augen-Prinzip (Aktionstyp `document.delete`, unabhängig von
    der bereits bestehenden retentionsgetriggerten `document.force_delete`)
    zurückgestellt."""

    status: Literal["trashed", "pending_approval"]
    document: DocumentOut | None = None
    approval_request_id: str | None = None


class UploadConfigIn(BaseModel):
    # Leer = keine Einschränkung (Default, siehe models.UploadConfig).
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
    # None = Basis gilt für diese Kategorie, sonst expliziter Override.
    log_viewed: bool | None = None
    log_downloaded: bool | None = None


class AuditTraceRoleOverrideOut(AuditTraceRoleOverrideIn):
    model_config = {"from_attributes": True}

    role: str
    updated_at: datetime


class RetentionUpdate(BaseModel):
    """Aufbewahrung/Zwangslöschung setzen (5.2/5.2a, seit P7-S1). `reason`
    wird serverseitig gegen `RetentionConfig`/`ObjectType`-Override geprüft
    (Pflicht/optional) - siehe main.py._resolve_deletion_reason_required."""

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


class DeletionRegisterEntryOut(BaseModel):
    id: str
    document_id: str
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


class CascadeTrashRequest(BaseModel):
    """Interner Service-zu-Service-Aufruf von `folder-service` (5.2, seit
    P7-S1b) - siehe main.py `POST /documents/cascade-trash`."""

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
    """Rückruf von `archival-service`, sobald die Archivkopie verifiziert
    ist (5.6, seit P7-S3) - `archive_format` ist `"pdf_a"` (LibreOffice-
    PDF/A-Export-Filter oder bestehende `pypdf`-Tagging-Logik)."""

    archive_format: str


class ArchiveStatusOut(BaseModel):
    document_id: str
    archive_after: datetime | None
    archived_at: datetime | None
    archive_format: str | None
    dehydrated_at: datetime | None


class HasActiveHoldOut(BaseModel):
    has_active_hold: bool
