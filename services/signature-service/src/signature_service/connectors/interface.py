from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class SignerInfo:
    """Identity that flows into the issued certificate (see
    `InternalSelfSignedConnector.sign`) - `display_name`/`email` come from
    the caller via a real `auth-service` account check, never taken
    unchecked from the client (`auth_client.py`)."""

    principal_id: str
    display_name: str
    email: str


@dataclass
class SignedResult:
    signed_pdf_bytes: bytes
    certificate_subject: str
    certificate_serial: str
    certificate_not_before: datetime
    certificate_not_after: datetime


@dataclass
class VerificationResult:
    integrity_intact: bool
    trusted: bool
    errors: list[str]


class SignatureProviderConnector(ABC):
    """Uniform interface for signature provider connector plugins (concept
    3.10, plugin principle like the storage backends/CMIS, 3.3): new
    providers (especially an accredited external QTSP for QES) only
    implement this interface - the rest of the Signature Service stays
    unchanged ("plug in alongside" principle, as with `StorageBackend`)."""

    @abstractmethod
    async def sign(self, pdf_bytes: bytes, *, signer: SignerInfo, level: str) -> SignedResult: ...

    @abstractmethod
    async def verify(self, pdf_bytes: bytes) -> VerificationResult: ...
