from datetime import datetime

from pydantic import BaseModel


class AuditEventOut(BaseModel):
    id: int
    event_id: str
    event_type: str
    occurred_at: datetime
    service_name: str
    subject: str | None
    payload: dict
    actor: str | None
    recorded_at: datetime
    prev_hash: str
    hash: str

    model_config = {"from_attributes": True}


class ChainVerificationOut(BaseModel):
    ok: bool
    checked: int
    broken_at_id: int | None
