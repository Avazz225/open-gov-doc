from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ObjectTypeCreate(BaseModel):
    name: str
    applies_to: Literal["document", "folder"]
    attributes: list[dict] = []
    naming_constraints: dict | None = None
    conditions: list[dict] = []


class ObjectTypeUpdate(BaseModel):
    attributes: list[dict] = []
    naming_constraints: dict | None = None
    conditions: list[dict] = []


class ObjectTypeOut(BaseModel):
    id: int
    name: str
    applies_to: str
    attributes: list[dict]
    naming_constraints: dict | None
    conditions: list[dict]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ValidateRequest(BaseModel):
    name: str
    attributes: dict = {}


class ValidateResult(BaseModel):
    valid: bool
    errors: list[str]
