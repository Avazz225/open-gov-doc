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


class CheckResult(BaseModel):
    allowed: bool
