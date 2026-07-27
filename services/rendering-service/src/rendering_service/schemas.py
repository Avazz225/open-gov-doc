from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class RenditionOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    document_id: str
    version_number: int
    rendition_type: str
    source_filename: str
    source_content_type: str | None
    target_filename: str
    target_content_type: str
    size_bytes: int
    status: Literal["ready", "failed"]
    error_message: str | None
    created_at: datetime
    updated_at: datetime
