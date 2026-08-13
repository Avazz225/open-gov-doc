from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class RoleCreate(BaseModel):
    name: str
    description: str = ""
    permissions: list[str] = []


class RoleUpdate(BaseModel):
    """Seit P12-S3 (7.3, Konfigurationsimport) - bislang gab es keinen Weg,
    eine bestehende Rolle zu aktualisieren (nur Anlegen/Auflisten), obwohl
    `folder-service`/`document-service` u. ä. ihre Update-Pendants längst
    haben. `name` bleibt unveränderlich (natürlicher Schlüssel für den
    Konfigurationsimport-Abgleich per Name)."""

    description: str
    permissions: list[str]


class RoleOut(BaseModel):
    id: int
    name: str
    description: str
    permissions: list[str]

    model_config = {"from_attributes": True}


class RoleAssignmentCreate(BaseModel):
    principal_type: Literal["user", "group"]
    principal_id: str
    role_id: int
    resource_id: str


class RoleAssignmentOut(BaseModel):
    id: int
    principal_type: Literal["user", "group"]
    principal_id: str
    role_id: int
    resource_id: str

    model_config = {"from_attributes": True}


class RoleAssignmentActionResult(BaseModel):
    """Wie `ScopeLockActionResult` (4.3/4.7) - `POST /role-assignments` kann
    seit P17-S3 optional per generischem Vier-Augen-Mechanismus gegated sein
    (`permission.role_assignment.create`, 14.2 "Berechtigungsänderung")."""

    status: Literal["created", "pending_approval"]
    role_assignment: RoleAssignmentOut | None = None
    approval_request_id: str | None = None


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


# --- Stellvertretung bei Abwesenheit (4.4a, P14-S11) -------------------------


class DelegationCreate(BaseModel):
    deputy_principal_id: str
    starts_at: datetime
    ends_at: datetime
    scope_object_type_ids: list[int] | None = None
    scope_process_definition_ids: list[int] | None = None
    scope_folder_resource_ids: list[str] | None = None


class DelegationOut(BaseModel):
    id: str
    delegator_principal_id: str
    deputy_principal_id: str
    starts_at: datetime
    ends_at: datetime
    scope_object_type_ids: list[int] | None
    scope_process_definition_ids: list[int] | None
    scope_folder_resource_ids: list[str] | None
    created_at: datetime
    revoked_at: datetime | None
    revoked_by: str | None

    model_config = {"from_attributes": True}


class DelegationCheckResult(BaseModel):
    allowed: bool
