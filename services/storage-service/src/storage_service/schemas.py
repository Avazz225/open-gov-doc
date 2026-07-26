from datetime import datetime

from pydantic import BaseModel


class ObjectMetadataOut(BaseModel):
    object_key: str
    backend: str
    checksum_sha256: str
    size_bytes: int
    content_type: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VerifyResult(BaseModel):
    ok: bool
    expected: str
    actual: str


class ObjectCopyOut(BaseModel):
    object_key: str
    backend_id: str
    status: str
    checksum_sha256: str | None
    attempts: int
    last_error: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FixityEntry(BaseModel):
    backend_id: str
    status: str
    ok: bool | None
    expected: str | None = None
    actual: str | None = None


class ReplicationRunResult(BaseModel):
    processed: int
    succeeded: int
    failed: int
    permanently_failed: int
