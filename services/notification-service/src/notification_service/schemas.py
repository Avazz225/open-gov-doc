from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Channel = Literal["email", "in_app", "webhook"]


class NotificationCreate(BaseModel):
    channel: Channel
    recipient: str
    subject: str
    body: str


class NotificationOut(BaseModel):
    id: int
    channel: Channel
    recipient: str
    subject: str
    body: str
    status: Literal["sent", "failed", "failed_permanent"]
    error: str | None
    attempts: int
    next_retry_at: datetime | None
    created_at: datetime
    sent_at: datetime | None

    model_config = {"from_attributes": True}


# --- Configurable email templates (post-roadmap phase 30, ADR 0111) -------


class EmailTemplateIn(BaseModel):
    subject_template: str
    body_template: str


class EmailTemplateOut(BaseModel):
    id: int
    use_case: str
    recipient_domain_pattern: str | None
    subject_template: str
    body_template: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmailTemplateUseCaseOut(BaseModel):
    use_case: str
    description: str
    placeholders: list[str]
