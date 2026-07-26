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
