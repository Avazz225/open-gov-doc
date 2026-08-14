from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SignatureLevel = Literal["ses", "aes", "qes"]


class SignatureCreate(BaseModel):
    document_id: str
    level: SignatureLevel
    signer_principal_id: str
    # If not specified, the current main version is signed. An explicitly
    # specified version that is no longer current produces a conflict copy
    # when checking in at document-service instead of advancing the main
    # version (optimistic conflict detection, 4.2) - both cases yield a
    # valid, permanently retrievable signed version.
    version_number: int | None = None
    reason: str | None = None


class SignatureOut(BaseModel):
    id: int
    document_id: str
    source_version_number: int
    version_number: int
    level: SignatureLevel
    connector_id: str
    signer_principal_id: str
    signer_display_name: str
    certificate_subject: str
    certificate_serial: str
    certificate_not_before: datetime
    certificate_not_after: datetime
    reason: str | None
    signed_at: datetime

    model_config = {"from_attributes": True}


class VerificationOut(BaseModel):
    valid: bool
    integrity_intact: bool
    certificate_expired: bool
    errors: list[str]


class SignatureProviderStatusOut(BaseModel):
    """A configured connector (3.10, post-roadmap phase 22 session 6) -
    `id`/`type` are structurally fixed (`Settings.signature_providers`),
    `levels` is the currently effective, admin-editable value."""

    id: str
    type: Literal["internal", "qtsp"]
    levels: list[SignatureLevel]


class SignatureProviderLevelsIn(BaseModel):
    id: str
    levels: list[SignatureLevel]
