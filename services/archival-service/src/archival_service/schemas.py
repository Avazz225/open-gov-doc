from datetime import datetime

from pydantic import BaseModel


class ArchivalTransferOut(BaseModel):
    id: str
    document_id: str
    status: str
    archive_format: str | None
    encrypted: bool
    storage_object_key: str | None
    checksum_sha256: str | None
    error_message: str | None
    locked_at: datetime | None
    copied_at: datetime | None
    verified_at: datetime | None
    released_at: datetime | None
    dehydrated_at: datetime | None
    rehydrated_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
