from datetime import datetime
from typing import Literal

from pydantic import BaseModel


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
    status: Literal["ready", "needs_review", "failed"]
    engine: str
    average_confidence: float
    full_text: str
    pages: list[OcrPageOut]
    error_message: str | None
    created_at: datetime
    updated_at: datetime
