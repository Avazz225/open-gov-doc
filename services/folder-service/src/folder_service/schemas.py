from datetime import datetime

from pydantic import BaseModel


class FolderCreate(BaseModel):
    name: str
    parent_id: str = "root"
    object_type_id: int | None = None
    attributes: dict = {}
    created_by: str


class FolderUpdate(BaseModel):
    name: str | None = None
    parent_id: str | None = None
    attributes: dict | None = None


class FolderOut(BaseModel):
    id: str
    name: str
    parent_id: str | None
    object_type_id: int | None
    attributes: dict
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
