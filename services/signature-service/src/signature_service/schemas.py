from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

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
    last_timestamped_at: datetime | None

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


# --- Incoming-signature validation (P92-S1, ADR 0248) ----------------------


class IncomingValidationConfigIn(BaseModel):
    enabled: bool
    revocation_mode: Literal["soft-fail", "hard-fail"]
    outbound_fetching: bool
    # P92-S2; defaults to on so a P92-S1 client keeps working.
    eu_trusted_lists: bool = True


class IncomingValidationConfigOut(IncomingValidationConfigIn):
    model_config = ConfigDict(from_attributes=True)
    updated_at: datetime
    updated_by: str | None


class IncomingTrustAnchorIn(BaseModel):
    label: str = Field(min_length=1, max_length=256)
    certificate_pem: str = Field(min_length=1, max_length=64 * 1024)


class IncomingTrustAnchorOut(BaseModel):
    """`source="internal"` is the installation's own CA: listed for
    completeness, not removable (`id` is `None`)."""

    id: int | None
    source: Literal["internal", "installation"]
    label: str
    subject: str
    fingerprint_sha256: str
    not_after: datetime
    created_at: datetime | None = None
    created_by: str | None = None


class IncomingSignatureValidationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    run_id: str
    version_number: int
    trigger: str
    format: str
    position: str | None
    kind: str
    indication: str
    sub_indication: str | None
    qualification: str
    trust_source: str | None
    signer_subject: str | None
    signer_issuer: str | None
    signer_serial: str | None
    claimed_signing_time: datetime | None
    best_signature_time: datetime | None
    coverage: str
    modification_level: str | None
    qc_type: str | None = None
    report_available: bool = False
    failure_reason: str | None
    technical_detail: str | None
    revocation_mode: str
    validated_at: datetime


class IncomingSignatureSummaryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version_number: int
    run_id: str
    signature_count: int
    worst_indication: str
    best_qualification: str
    changed_after_signing: bool
    truncated: bool
    validated_at: datetime


class IncomingSignaturesOut(BaseModel):
    document_id: str
    summaries: list[IncomingSignatureSummaryOut]
    validations: list[IncomingSignatureValidationOut]


class RevalidateIn(BaseModel):
    document_id: str
    version_number: int | None = None


class RevalidateOut(BaseModel):
    document_id: str
    version_number: int


class BackfillIn(BaseModel):
    folder_id: str | None = None
    created_from: datetime | None = None
    created_to: datetime | None = None
    only_unvalidated: bool = True


class BackfillStatusOut(BaseModel):
    state: str
    started_at: datetime | None
    finished_at: datetime | None
    started_by: str | None
    criteria: dict
    scanned: int
    queued: int
    skipped_validated: int
    limit_reached: bool
    error: str | None
    queue_pending: int | None = None


class TrustedListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    kind: str
    territory: str
    location_uri: str | None
    sequence_number: int | None
    issued_at: datetime | None
    next_update: datetime | None
    fetched_at: datetime
    source: str


class TrustedListsOut(BaseModel):
    lists: list[TrustedListOut]
    last_fetch: dict | None
    poll_interval_seconds: int
    territories: list[str]
