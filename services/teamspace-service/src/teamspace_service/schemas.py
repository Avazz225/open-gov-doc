from datetime import datetime

from pydantic import BaseModel


class TeamspaceCreate(BaseModel):
    name: str
    description: str = ""


class TeamspaceOut(BaseModel):
    id: str
    name: str
    description: str
    root_folder_id: str
    created_by: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TeamspaceAdminOut(BaseModel):
    """Installation-wide overview (Post-Roadmap Phase 22 Session 5) -
    identical to `TeamspaceOut`, plus `member_count` instead of a full
    member list (sufficient for a pure overview endpoint, avoids a
    second, gated members route)."""

    id: str
    name: str
    description: str
    root_folder_id: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    member_count: int


class TeamspaceMemberInvite(BaseModel):
    principal_id: str
    can_manage_members: bool = False


class TeamspaceMemberUpdate(BaseModel):
    can_manage_members: bool


class TeamspaceMemberOut(BaseModel):
    id: int
    teamspace_id: str
    principal_id: str
    can_manage_members: bool
    invited_by: str
    invited_at: datetime

    model_config = {"from_attributes": True}


class TeamspaceAppointmentCreate(BaseModel):
    title: str
    description: str = ""
    start_at: datetime
    end_at: datetime


class TeamspaceAppointmentOut(BaseModel):
    id: int
    teamspace_id: str
    title: str
    description: str
    start_at: datetime
    end_at: datetime
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TeamspaceContactCreate(BaseModel):
    name: str
    email: str | None = None
    phone: str | None = None
    note: str = ""


class TeamspaceContactOut(BaseModel):
    id: int
    teamspace_id: str
    name: str
    email: str | None
    phone: str | None
    note: str
    created_by: str
    created_at: datetime

    model_config = {"from_attributes": True}
