"""Format-agnostic validator contract (ADR 0248 Decision 2).

A validator says whether it applies to a version (by content type and file
name) and turns the bytes into one `SignatureFinding` per signature. Nothing
PDF-specific lives at the top level of a finding: PAdES puts its field name
into `position`, a future CAdES validator its file pairing.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Protocol

# ETSI EN 319 102-1 main indications.
Indication = Literal["TOTAL-PASSED", "TOTAL-FAILED", "INDETERMINATE"]
# Worst first - used for the per-version summary.
INDICATION_RANK: dict[str, int] = {"TOTAL-FAILED": 0, "INDETERMINATE": 1, "TOTAL-PASSED": 2}

Qualification = Literal["qes", "adesqc", "ades", "unknown"]
QUALIFICATION_RANK: dict[str, int] = {"unknown": 0, "ades": 1, "adesqc": 2, "qes": 3}

# What the signature covers:
# - "entire_document": nothing was added after signing;
# - "permitted_changes": later revisions only did what signing allows (form
#   filling, annotations, validation data and timestamps);
# - "changed_after_signing": anything else, the shadow-attack case;
# - "unknown": the file couldn't be analysed.
Coverage = Literal["entire_document", "permitted_changes", "changed_after_signing", "unknown"]

# Where a trusted path ended. "eu_trusted_list" arrives with P92-S2.
TrustSource = Literal["internal", "installation", "eu_trusted_list"]

SignatureKind = Literal["signature", "certification", "document_timestamp", "file"]


@dataclass(frozen=True)
class SignatureFinding:
    format: str
    position: str | None
    kind: SignatureKind
    indication: Indication
    sub_indication: str | None
    qualification: Qualification = "unknown"
    trust_source: TrustSource | None = None
    signer_subject: str | None = None
    signer_issuer: str | None = None
    signer_serial: str | None = None
    claimed_signing_time: datetime | None = None
    best_signature_time: datetime | None = None
    coverage: Coverage = "unknown"
    modification_level: str | None = None
    # Plain German for the UI; technical detail goes to `technical_detail`.
    failure_reason: str | None = None
    technical_detail: str | None = None


@dataclass(frozen=True)
class TrustAnchorInput:
    """A certificate to trust, with where it came from."""

    certificate_der: bytes
    source: TrustSource


@dataclass(frozen=True)
class ValidationPolicy:
    """Everything a run needs besides the bytes - a snapshot, so a run in a
    worker thread never touches the database or `Settings`."""

    anchors: list[TrustAnchorInput]
    revocation_mode: Literal["soft-fail", "hard-fail"]
    outbound_fetching: bool
    max_signatures: int
    max_path_length: int
    time_limit_seconds: float
    fetch: "FetchLimits"


@dataclass(frozen=True)
class FetchLimits:
    per_request_timeout_seconds: float = 10.0
    max_response_bytes: int = 2 * 1024 * 1024
    proxy: str | None = None
    # Only for tests with a local revocation server on a random port: lifts
    # the address and port checks. No setting maps to it, so a deployment
    # can't turn it on.
    allow_local_test_server: bool = False
    extra_blocked_networks: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ValidationOutcome:
    findings: list[SignatureFinding]
    # More signatures than `max_signatures`: only the first ones were checked.
    truncated: bool = False


class SignatureValidator(Protocol):
    format: str

    def applies(self, content_type: str | None, filename: str | None) -> bool: ...

    async def validate(self, data: bytes, policy: ValidationPolicy) -> ValidationOutcome: ...
