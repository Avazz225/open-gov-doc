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


class InboundMessageOut(BaseModel):
    id: str
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

    model_config = {"from_attributes": True}


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
