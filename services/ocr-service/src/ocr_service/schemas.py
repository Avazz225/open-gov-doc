from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class OcrWordOut(BaseModel):
    text: str
    left: float
    top: float
    width: float
    height: float
    confidence: float


class OcrPageOut(BaseModel):
    page_number: int
    width: int
    height: int
    words: list[OcrWordOut]


class OcrResultOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    document_id: str
    version_number: int
    status: Literal["ready", "needs_review", "failed", "failed_permanent", "skipped"]
    engine: str
    average_confidence: float
    full_text: str
    pages: list[OcrPageOut]
    error_message: str | None
    attempts: int
    next_retry_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OcrConfigIn(BaseModel):
    # None = keine Obergrenze.
    max_word_count: int | None = Field(default=None, ge=1)
    batch_size: int = Field(default=4, ge=1, le=64)
    # Leer = keine Einschränkung (Default, siehe models.OcrConfig).
    allowed_content_types: list[str] = Field(default_factory=list)


class OcrConfigOut(OcrConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime
