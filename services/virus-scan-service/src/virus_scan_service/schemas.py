from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ScanResultOut(BaseModel):
    id: str
    document_id: str | None
    filename: str
    content_type: str | None
    size_bytes: int
    checksum_sha256: str
    status: Literal["clean", "infected", "released", "purged"]
    threat_name: str | None
    engine: str
    quarantine_object_key: str | None
    created_by: str | None
    scanned_at: datetime
    resolved_by: str | None
    resolved_at: datetime | None

    model_config = {"from_attributes": True}


class QuarantineReleaseRequest(BaseModel):
    """Information that is missing for creating the document that was
    originally never generated, and that must now be supplied at release
    time (2.5, P15-S2)."""

    title: str
    folder_id: str | None = None
    object_type_id: int | None = None
    attributes: dict = {}
