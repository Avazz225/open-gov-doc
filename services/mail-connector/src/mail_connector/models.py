from datetime import datetime

from dms_db_base import make_declarative_base
from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

Base = make_declarative_base("mail_connector")


class InboundMessage(Base):
    """A message retrieved via the inbound mail path, not yet (or already)
    assigned (2.5/10.3). `source_uid` is the backend's own stable identifier
    (POP3 UIDL) - basis for the idempotency check, so that the same poll
    tick doesn't create an already-processed message twice."""

    __tablename__ = "inbound_message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    source_uid: Mapped[str] = mapped_column(String(255), unique=True)
    from_address: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(998))
    body_text: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # "unassigned" (no/ambiguous match) | "proposed_match" (exactly one
    # reference number/case number match, awaiting confirmation) |
    # "confirmed" (assigned, document(s) created) | "rejected" (discarded by
    # the mail room, e.g. spam).
    status: Mapped[str] = mapped_column(String(16), default="unassigned")
    match_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # "kennzeichen"|"vorgangsnummer"
    match_value: Mapped[str | None] = mapped_column(String(64), nullable=True)
    proposed_target_type: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # "document"|"case"
    proposed_target_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # All candidate tokens found in the subject/body (even with 0/N matches)
    # - transparency for manual assignment by the mail room, see
    # matching.py.
    match_candidates: Mapped[list] = mapped_column(JSON, default=list)
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejected_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)


class InboundAttachment(Base):
    """An attachment of an `InboundMessage` (2.5/10.3) - already goes through
    the mandatory virus scan upon arrival (see `virus_scan_client.py`); a
    clean attachment is stored under `storage_object_key` until assignment,
    an infected one ends up exclusively in the already-existing quarantine
    (P15-S2) - no duplicate storage."""

    __tablename__ = "inbound_attachment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("mail_connector.inbound_message.id"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512))
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    scan_id: Mapped[str] = mapped_column(String(36))
    scan_status: Mapped[str] = mapped_column(String(16))  # "clean" | "infected"
    # Only set when scan_status="clean" - temporary storage until
    # assignment, then deleted (see repository/main.py confirm/assign).
    storage_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    resulting_document_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


class OutboundMessage(Base):
    """Outbound mail (2.5) - every external correspondence sent via
    `POST /outbound`, auditable with an optional reference to the
    triggering document/circulation folder."""

    __tablename__ = "outbound_message"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    to_address: Mapped[str] = mapped_column(String(320))
    subject: Mapped[str] = mapped_column(String(998))
    body: Mapped[str] = mapped_column(String)
    related_document_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    related_case_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sent_by: Mapped[str] = mapped_column(String(128))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16))  # "sent" | "failed"
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)
