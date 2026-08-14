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
