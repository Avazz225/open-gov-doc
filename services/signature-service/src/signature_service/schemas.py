from datetime import datetime
from typing import Literal

from pydantic import BaseModel

SignatureLevel = Literal["ses", "aes", "qes"]


class SignatureCreate(BaseModel):
    document_id: str
    level: SignatureLevel
    signer_principal_id: str
    # Ohne Angabe wird die aktuelle Hauptversion signiert. Eine explizit
    # angegebene, nicht mehr aktuelle Version erzeugt beim Einchecken bei
    # document-service eine Konfliktkopie statt die Hauptversion zu
    # verschieben (optimistische Konflikterkennung, 4.2) - beide Fälle
    # liefern eine gültige, dauerhaft abrufbare signierte Version.
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
    """Ein konfigurierter Connector (3.10, Post-Roadmap Phase 22 Session 6) -
    `id`/`type` sind strukturell fest (`Settings.signature_providers`),
    `levels` ist der aktuell wirksame, admin-editierbare Wert."""

    id: str
    type: Literal["internal", "qtsp"]
    levels: list[SignatureLevel]


class SignatureProviderLevelsIn(BaseModel):
    id: str
    levels: list[SignatureLevel]
