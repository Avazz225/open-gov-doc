from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

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


ReminderStatus = Literal["open", "done"]


class ReminderCreate(BaseModel):
    """P90-S1 (ADR 0246). `user_id` must be the caller's own user name, as for
    favorites."""

    user_id: str
    object_type: ObjectType
    object_id: str
    # With a time zone: a reminder is a point in time, and "09:00" without
    # one would mean the server's zone.
    due_at: AwareDatetime
    note: str = Field(default="", max_length=2000)


class ReminderUpdate(BaseModel):
    """Edit the date and/or the note of an open reminder. A new date that is
    later than now re-arms the due notification."""

    due_at: AwareDatetime | None = None
    note: str | None = Field(default=None, max_length=2000)


class ReminderSnooze(BaseModel):
    until: AwareDatetime


class ReminderOut(BaseModel):
    id: str
    user_id: str
    object_type: ObjectType
    object_id: str
    due_at: datetime
    note: str
    status: ReminderStatus
    notified_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime
    # Computed: open and `due_at` has passed - what the "due" view shows.
    is_due: bool = False

    model_config = {"from_attributes": True}


WatchObjectType = Literal["document", "folder"]


class WatchCreate(BaseModel):
    """P90-S4 (ADR 0256). `user_id` must be the caller's own user name."""

    user_id: str
    object_type: WatchObjectType
    object_id: str


class WatchOut(BaseModel):
    id: str
    user_id: str
    object_type: WatchObjectType
    object_id: str
    created_at: datetime

    model_config = {"from_attributes": True}
