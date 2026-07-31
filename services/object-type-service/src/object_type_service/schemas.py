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


class ObjectTypeUpdate(BaseModel):
    attributes: list[dict] = []
    naming_constraints: dict | None = None
    conditions: list[dict] = []
    allowed_parent_types: list[str] | None = None
    icon: str | None = None
    kennzeichen_format: str | None = None
    kennzeichen_display_override: bool | None = None


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
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class KennzeichenOut(BaseModel):
    kennzeichen: str


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
