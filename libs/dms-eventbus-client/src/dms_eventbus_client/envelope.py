import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field


class Event(BaseModel):
    """Shared wire format for all published events (concept 3.4/5.3).

    Every producer uses the same envelope, so consumers (chiefly the audit
    service) can process events from different services uniformly, without
    needing to know a separate format per producer.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    service_name: str
    subject: str | None = None
    payload: dict = Field(default_factory=dict)
    # Acting person (first-class instead of an ad-hoc payload convention,
    # since P7-S2, prerequisite for the forensic trace 5.4b) - username where
    # a human triggered the action, otherwise "system:<component>" (same
    # convention already used by document-service/folder-services
    # "system:retention-poll"). None only for legacy events predating this field.
    actor: str | None = None
    # Representation during absence (4.4a, since P14-S11): set when
    # ``actor`` acted on behalf of another person (concept wording 4.4a/5.3,
    # "an additional note of on whose behalf it was performed") -
    # deliberately a SECOND field instead of overwriting ``actor``: the
    # acting identity always remains the person who actually logged in, no
    # identity switch. None for every normal, non-delegated action (the
    # vast majority case).
    on_behalf_of: str | None = None

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_bytes(cls, data: bytes) -> "Event":
        return cls.model_validate_json(data)
