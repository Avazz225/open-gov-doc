from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ObjectTypeCreate(BaseModel):
    name: str
    applies_to: Literal["document", "folder"]
    attributes: list[dict] = []
    naming_constraints: dict | None = None
    conditions: list[dict] = []
    allowed_parent_types: list[str] | None = None
    icon: str | None = None


class ObjectTypeUpdate(BaseModel):
    attributes: list[dict] = []
    naming_constraints: dict | None = None
    conditions: list[dict] = []
    allowed_parent_types: list[str] | None = None
    icon: str | None = None


class ObjectTypeOut(BaseModel):
    id: int
    name: str
    applies_to: str
    attributes: list[dict]
    naming_constraints: dict | None
    conditions: list[dict]
    allowed_parent_types: list[str] | None
    icon: str | None
    created_at: datetime
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
