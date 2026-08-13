from datetime import datetime
from typing import Literal

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
    attempts: int
    next_retry_at: datetime | None
    locked_at: datetime | None
    copied_at: datetime | None
    verified_at: datetime | None
    released_at: datetime | None
    dehydrated_at: datetime | None
    rehydrated_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReleasedItemOut(BaseModel):
    """Aussonderungs-Zugriffsbereich (2.5/5.6, P15-S5) - hydratisierte Sicht
    auf `released`-Transfers, siehe `browse.build_released_items`."""

    transfer_id: str
    kind: Literal["document", "case"]
    subject_id: str
    title: str
    identifier: str | None
    released_at: datetime | None
    purge_at: datetime | None


class CaseArchivalTransferOut(BaseModel):
    id: str
    case_id: str
    status: str
    encrypted: bool
    storage_object_key: str | None
    checksum_sha256: str | None
    error_message: str | None
    attempts: int
    next_retry_at: datetime | None
    locked_at: datetime | None
    packaged_at: datetime | None
    verified_at: datetime | None
    released_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
