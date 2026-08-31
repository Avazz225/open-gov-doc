from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class InboundAttachmentOut(BaseModel):
    id: int
    filename: str
    content_type: str | None
    size_bytes: int
    scan_status: Literal["clean", "infected"]
    scan_id: str
    resulting_document_id: str | None

    model_config = {"from_attributes": True}


class MailboxOut(BaseModel):
    """Read-only reflection of a configured `settings.MailboxConfig` (14.2,
    Post-Roadmap Phase 31 Session 12a) - deliberately excludes every
    credential field (host/port/username/password), only what's needed to
    render a mailbox selector/label in the frontend."""

    id: str
    name: str
    kind: Literal["central", "departmental"]
    owning_group_id: str | None


class MailRoutingLogEntryOut(BaseModel):
    id: int
    from_mailbox_id: str
    to_mailbox_id: str
    routed_by: str
    routed_at: datetime
    reason: str | None

    model_config = {"from_attributes": True}


class InboundMessageOut(BaseModel):
    id: str
    mailbox_id: str
    from_address: str
    subject: str
    body_text: str
    received_at: datetime
    status: Literal["unassigned", "proposed_match", "confirmed", "rejected"]
    match_type: Literal["kennzeichen", "vorgangsnummer"] | None
    match_value: str | None
    proposed_target_type: Literal["document", "case"] | None
    proposed_target_id: str | None
    match_candidates: list[str]
    confirmed_by: str | None
    confirmed_at: datetime | None
    rejected_reason: str | None
    attachments: list[InboundAttachmentOut] = []
    # Routing history (14.2, Post-Roadmap Phase 31 Session 12b) - every hop
    # this message has been routed through, oldest first. Embedded the same
    # way `attachments` already is - the standalone, cross-message
    # searchable log view is P31-S12c, not this session.
    routing_log: list[MailRoutingLogEntryOut] = []

    model_config = {"from_attributes": True}


class RouteMessageRequest(BaseModel):
    target_mailbox_id: str
    reason: str | None = None


class ConfirmMatchRequest(BaseModel):
    title: str
    folder_id: str | None = None


class AssignRequest(BaseModel):
    title: str
    folder_id: str
    case_id: str | None = None


class RejectRequest(BaseModel):
    reason: str | None = None


class OutboundMessageCreate(BaseModel):
    to_address: str
    subject: str
    body: str
    related_document_id: str | None = None
    related_case_id: str | None = None


class OutboundMessageOut(BaseModel):
    id: str
    to_address: str
    subject: str
    body: str
    related_document_id: str | None
    related_case_id: str | None
    sent_by: str
    sent_at: datetime
    status: Literal["sent", "failed"]
    error_message: str | None

    model_config = {"from_attributes": True}
