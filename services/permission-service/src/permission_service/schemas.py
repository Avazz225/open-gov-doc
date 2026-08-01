from datetime import datetime
from typing import Literal

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


class BatchCheckRequest(BaseModel):
    principal_id: str
    permission: str
    access_type: Literal["read", "write"] = "read"
    resource_ids: list[str]


class BatchCheckResult(BaseModel):
    results: dict[str, bool]


class ApprovalActionConfigOut(BaseModel):
    action_type: str
    requires_approval: bool
    required_permission: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class ApprovalActionConfigUpdate(BaseModel):
    requires_approval: bool
    required_permission: str | None = None


class ApprovalRequestCreate(BaseModel):
    action_type: str
    initiated_by: str
    payload: dict = {}


class ApprovalRequestOut(BaseModel):
    id: str
    action_type: str
    initiated_by: str
    payload: dict
    status: str
    approved_by: str | None
    rejected_by: str | None
    reason: str | None
    created_at: datetime
    decided_at: datetime | None

    model_config = {"from_attributes": True}


class ApprovalDecision(BaseModel):
    approved_by: str


class ApprovalRejection(BaseModel):
    rejected_by: str
    reason: str | None = None


class ScopeLockActionResult(BaseModel):
    status: Literal["created", "released", "pending_approval"]
    scope_lock: ScopeLockOut | None = None
    approval_request_id: str | None = None


class MaintenanceModeOut(BaseModel):
    active: bool
    reason: str | None
    triggered_by: str | None
    activated_at: datetime | None
    lifted_by: str | None
    lifted_at: datetime | None

    model_config = {"from_attributes": True}


class MaintenanceModeTrigger(BaseModel):
    triggered_by: str
    reason: str | None = None


class MaintenanceModeLift(BaseModel):
    lifted_by: str


class MaintenanceModeActionResult(BaseModel):
    status: Literal["activated", "pending_approval"]
    maintenance_mode: MaintenanceModeOut | None = None
    approval_request_id: str | None = None
