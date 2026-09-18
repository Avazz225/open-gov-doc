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
    reviewed_at: datetime | None
    reviewed_by: str | None
    created_at: datetime
    updated_at: datetime


class OcrResultReviewedIn(BaseModel):
    """Body of the `POST /ocr-results/{id}/reviewed` connector-call callback
    (Phase 45 Session 3) - `reviewed_by` is optional since it only arrives
    if the reviewer typed it into `reviewer-ui`'s generic completion-data
    textarea (`TaskList.tsx`'s `dataJson` field merges into the workflow
    instance's process data, which is exactly the payload this endpoint
    receives - no reviewer-ui change needed for this to work). Extra keys
    (`document_id`/`ocr_result_id`/`average_confidence`, always present
    from the instance's own `initial_data`) are accepted and ignored -
    pydantic's default `extra="ignore"`."""

    reviewed_by: str | None = None


class OcrConfigIn(BaseModel):
    # None = no upper limit.
    max_word_count: int | None = Field(default=None, ge=1)
    batch_size: int = Field(default=4, ge=1, le=64)
    # Empty = no restriction (default, see models.OcrConfig).
    allowed_content_types: list[str] = Field(default_factory=list)


class OcrConfigOut(OcrConfigIn):
    model_config = {"from_attributes": True}

    updated_at: datetime
