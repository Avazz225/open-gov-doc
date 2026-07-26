from datetime import datetime

from pydantic import BaseModel


class RoleCreate(BaseModel):
    name: str
    description: str = ""
    permissions: list[str] = []


class RoleOut(BaseModel):
    id: int
    name: str
    description: str
    permissions: list[str]

    model_config = {"from_attributes": True}


class RoleAssignmentCreate(BaseModel):
    principal_type: str
    principal_id: str
    role_id: int
    resource_id: str


class RoleAssignmentOut(BaseModel):
    id: int
    principal_type: str
    principal_id: str
    role_id: int
    resource_id: str

    model_config = {"from_attributes": True}


class ResourceNodeUpdate(BaseModel):
    inherit: bool


class ResourceNodeOut(BaseModel):
    resource_id: str
    parent_id: str | None
    resource_type: str
    inherit: bool

    model_config = {"from_attributes": True}


class EffectivePermissionsOut(BaseModel):
    principal_id: str
    resource_id: str
    roles: list[str]
    permissions: list[str]


class ScopeLockCreate(BaseModel):
    resource_id: str
    locked_by: str
    reason: str | None = None
    blocks_read: bool = False
    expires_at: datetime | None = None


class ScopeLockRelease(BaseModel):
    released_by: str


class ScopeLockOut(BaseModel):
    id: int
    resource_id: str
    locked_by: str
    reason: str | None
    blocks_read: bool
    expires_at: datetime | None
    created_at: datetime
    released_at: datetime | None
    released_by: str | None

    model_config = {"from_attributes": True}


class CheckResult(BaseModel):
    allowed: bool
    blocked_by_scope_lock: bool = False
    scope_lock_reason: str | None = None
    scope_lock_expires_at: datetime | None = None
