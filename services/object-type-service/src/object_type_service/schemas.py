from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class LayoutPurpose(StrEnum):
    """Verwendungszweck eines Formular-Layouts (2.2b) - dasselbe Layout-Format
    steuert alle drei, aber als getrennt speicherbare/überschreibbare Zeilen."""

    display = "display"
    search = "search"
    upload = "upload"


SignatureLevel = Literal["ses", "aes", "qes"]

# Verschlusssachen-Einstufung (2.5, seit P17-S2 mehrstufig - 14.2): die vier
# gängigen deutschen VS-Einstufungen, wörtlich aus dem Konzepttext.
ClassificationLevel = Literal["VS-NfD", "VS-VERTRAULICH", "GEHEIM", "STRENG GEHEIM"]


class ObjectTypeCreate(BaseModel):
    name: str
    applies_to: Literal["document", "folder"]
    attributes: list[dict] = []
    naming_constraints: dict | None = None
    conditions: list[dict] = []
    allowed_parent_types: list[str] | None = None
    icon: str | None = None
    kennzeichen_format: str | None = None
    kennzeichen_display_override: bool | None = None
    required_signature_level: SignatureLevel | None = None
    default_retention_days: int | None = None
    deletion_reason_required_override: bool | None = None
    default_archive_after_days: int | None = None
    archive_encryption_enabled: bool = False
    classification_level: ClassificationLevel | None = None


class ObjectTypeUpdate(BaseModel):
    attributes: list[dict] = []
    naming_constraints: dict | None = None
    conditions: list[dict] = []
    allowed_parent_types: list[str] | None = None
    icon: str | None = None
    kennzeichen_format: str | None = None
    kennzeichen_display_override: bool | None = None
    required_signature_level: SignatureLevel | None = None
    default_retention_days: int | None = None
    deletion_reason_required_override: bool | None = None
    default_archive_after_days: int | None = None
    archive_encryption_enabled: bool = False
    classification_level: ClassificationLevel | None = None


class ObjectTypeOut(BaseModel):
    id: int
    name: str
    applies_to: str
    attributes: list[dict]
    naming_constraints: dict | None
    conditions: list[dict]
    allowed_parent_types: list[str] | None
    icon: str | None
    kennzeichen_format: str | None
    kennzeichen_display_override: bool | None
    required_signature_level: str | None
    default_retention_days: int | None
    deletion_reason_required_override: bool | None
    default_archive_after_days: int | None
    archive_encryption_enabled: bool
    classification_level: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KennzeichenOut(BaseModel):
    kennzeichen: str


class NextKennzeichenRequest(BaseModel):
    """Attributwerte des anzulegenden Dokuments (seit P17-S2, 14.2) - Grundlage
    für attributbasierte Kennzeichen-Platzhalter wie `{Federfuehrung}` (jeder
    Platzhalter, der kein Datums-/Zähler-Platzhalter ist, wird als Attributname
    interpretiert, siehe repository._render_kennzeichen). Leer/weggelassen
    bleibt für einen rein datums-/zählerbasierten Format-String wie zuvor
    (P5e-S1) folgenlos."""

    attributes: dict = {}


class KennzeichenConfigIn(BaseModel):
    show_before_filename: bool


class KennzeichenConfigOut(KennzeichenConfigIn):
    updated_at: datetime

    model_config = {"from_attributes": True}


class ValidateRequest(BaseModel):
    name: str
    attributes: dict = {}
    # Vom Aufrufer (Folder/Document Service) aufgelöste Platzierungs-Information
    # (2.2a) - bewusst als object_type_id/Root-Flag statt bereits aufgelöstem
    # Namen übertragen, damit Object-Type Service als einzige Quelle der
    # Namensauflösung dient (kein zusätzlicher Roundtrip beim Aufrufer nötig).
    parent_object_type_id: int | None = None
    parent_is_root: bool = False


class ValidateResult(BaseModel):
    valid: bool
    errors: list[str]


class LayoutField(BaseModel):
    attribute: str
    label: str
    required: bool = False


class LayoutRow(BaseModel):
    columns: list[LayoutField]


class LayoutIn(BaseModel):
    rows: list[LayoutRow] = []
    responsive_breakpoint_px: int = 600


class LayoutOut(LayoutIn):
    # False = generiertes Smart Layout (nicht persistiert), True = explizit
    # über PUT gespeicherte Abweichung (siehe ADR 0014).
    is_custom: bool
