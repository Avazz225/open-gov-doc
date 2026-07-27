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
    status: Literal["clean", "infected"]
    threat_name: str | None
    engine: str
    quarantine_object_key: str | None
    created_by: str | None
    scanned_at: datetime

    model_config = {"from_attributes": True}
