from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class SignerInfo:
    """Identität, die ins ausgestellte Zertifikat einfließt (siehe
    `InternalSelfSignedConnector.sign`) - `display_name`/`email` kommen vom
    Aufrufer aus einer echten `auth-service`-Kontenprüfung, nie ungeprüft vom
    Client übernommen (`auth_client.py`)."""

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
    """Einheitliches Interface für Signature-Provider-Connector-Plugins
    (Konzept 3.10, Plugin-Prinzip wie die Storage-Backends/CMIS, 3.3): neue
    Anbieter (insbesondere ein akkreditierter externer QTSP für QES)
    implementieren nur dieses Interface - der Rest des Signature Service
    bleibt unverändert ("Dazustellen"-Prinzip, wie bei `StorageBackend`)."""

    @abstractmethod
    async def sign(self, pdf_bytes: bytes, *, signer: SignerInfo, level: str) -> SignedResult: ...

    @abstractmethod
    async def verify(self, pdf_bytes: bytes) -> VerificationResult: ...
