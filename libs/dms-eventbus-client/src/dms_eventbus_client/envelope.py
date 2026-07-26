import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Event(BaseModel):
    """Gemeinsames Wire-Format für alle publizierten Ereignisse (Konzept 3.4/5.3).

    Jeder Producer nutzt dieselbe Hülle, damit Konsumenten (allen voran der
    Audit Service) Ereignisse verschiedener Services einheitlich verarbeiten
    können, ohne je Producer ein eigenes Format zu kennen.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    service_name: str
    subject: str | None = None
    payload: dict = Field(default_factory=dict)

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> "Event":
        return cls.model_validate_json(data)
