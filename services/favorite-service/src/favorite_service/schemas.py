from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ObjectType = Literal["document", "folder"]


class FavoriteCreate(BaseModel):
    user_id: str
    object_type: ObjectType
    object_id: str


class FavoriteOut(BaseModel):
    id: str
    user_id: str
    object_type: ObjectType
    object_id: str
    created_at: datetime

    model_config = {"from_attributes": True}
