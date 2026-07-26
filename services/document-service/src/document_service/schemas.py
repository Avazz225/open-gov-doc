from datetime import datetime

from pydantic import BaseModel


class DocumentOut(BaseModel):
    id: str
    title: str
    folder_id: str | None
    object_type_id: str | None
    current_version_number: int
    deleted_at: datetime | None
    created_by: str
    created_at: datetime
    updated_at: datetime

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
