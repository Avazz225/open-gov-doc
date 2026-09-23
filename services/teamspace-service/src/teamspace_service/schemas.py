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
    # `None` for a manually-invited member (Post-Roadmap Phase 74 Session
    # 3, ADR 0160/ADR 0217) - see `TeamspaceMember.source_ad_group_name`'s
    # own docstring.
    source_ad_group_name: str | None = None

    model_config = {"from_attributes": True}


class TeamspaceAdGroupBindingCreate(BaseModel):
    ad_group_name: str


class TeamspaceAdGroupBindingOut(BaseModel):
    id: int
    teamspace_id: str
    ad_group_name: str
    invited_by: str
    invited_at: datetime

    model_config = {"from_attributes": True}


class AdGroupMemberPreview(BaseModel):
    """Deliberately minimal, mirrors `auth-service`'s own `UserLookupOut`
    shape - `GET /teamspaces/{id}/ad-group-preview` is a thin proxy for
    it (Post-Roadmap Phase 74 Session 3, ADR 0160/ADR 0217)."""

    id: str
    username: str


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
