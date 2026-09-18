from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# "case" added in Phase 45 Session 2 - deliberately deferred at plan approval
# (P7-S1d) since case-service had no browsing UI yet to add a favorite toggle
# to; "Umlaufmappen" shipped in Phase 34 (ADR 0141), closing that dependency.
ObjectType = Literal["document", "folder", "case"]


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
