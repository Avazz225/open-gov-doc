from datetime import datetime

from pydantic import BaseModel


class ManagedInstallationCreate(BaseModel):
    display_name: str
    gateway_base_url: str
    # Optional: the operator can enter here the value already set on the
    # installation side via DMS_FLEET_AGENT_API_KEY, or leave it blank and
    # let this service generate one (in which case the operator must instead
    # transfer the returned value onto the installation) - exactly the same
    # pattern as migration-service's `PairedInstallationCreate`.
    fleet_agent_api_key: str | None = None


class ManagedInstallationOut(BaseModel):
    id: str
    display_name: str
    gateway_base_url: str
    created_at: datetime
    updated_at: datetime


class ManagedInstallationCreateOut(ManagedInstallationOut):
    """Only the initial response contains the plaintext key - never again
    afterward (same convention as `federation-hub-service`'s
    `InstallationRegisterOut` and `migration-service`'s
    `PairedInstallationCreateOut`)."""

    fleet_agent_api_key: str


class ManagedInstallationRotateKeyRequest(BaseModel):
    # Optional, same flexibility as `ManagedInstallationCreate` above: leave
    # it blank and let this service generate a new value (in which case the
    # operator must instead transfer it onto the installation), or enter a
    # value already set on the installation here (in which case this service
    # merely catches up).
    fleet_agent_api_key: str | None = None


class InstallationStatusOut(BaseModel):
    """Aggregated overview (3a: "basic health overview") - queried live,
    none of it is persisted here. ``reachable=False`` for every
    network/protocol error instead of a raised exception, so that an
    unreachable installation doesn't prevent the overview of the others
    (same principle as other poll loops in this project, e.g.
    `license-service`'s `poll_loop.py`)."""

    id: str
    display_name: str
    reachable: bool
    installation_id: str | None = None
    installation_display_name: str | None = None
    license_status: dict | None = None
    error: str | None = None


class LicenseUploadRequest(BaseModel):
    license_token: str


class ProvisionRequest(BaseModel):
    """`config_document` is deliberately a raw `dict` (identical format to
    `config-service`'s own export/import, 7.3) instead of its own, second
    configuration schema - this service does not curate a template library
    (that's Phase 17, concept §14), it just centrally passes along whatever
    the operator provides."""

    config_document: dict
    categories: list[str] | None = None


# --- Fleet update orchestration (3a extension, P13-S2b) --------------------


class GroupCreate(BaseModel):
    name: str


class GroupOut(BaseModel):
    id: str
    name: str
    created_at: datetime
    installation_ids: list[str]


class GroupMemberAdd(BaseModel):
    installation_id: str


class PlanStep(BaseModel):
    name: str
    step_type: str
    requires_approval: bool = False


class UpdatePlanCreate(BaseModel):
    name: str
    version: str
    steps: list[PlanStep]


class UpdatePlanOut(BaseModel):
    id: str
    name: str
    version: str
    steps: list[PlanStep]
    created_at: datetime


class RolloutCreate(BaseModel):
    plan_id: str
    name: str
    group_id: str | None = None
    include: list[str] = []
    exclude: list[str] = []


class RolloutStart(BaseModel):
    started_by: str


class InstallationRunOut(BaseModel):
    id: str
    installation_id: str
    installation_display_name: str
    current_step_index: int
    current_step_name: str | None
    status: str
    last_outcome: str | None
    error_message: str | None
    proposed_by: str | None
    started_at: datetime | None
    updated_at: datetime
    completed_at: datetime | None


class RolloutOut(BaseModel):
    id: str
    plan_id: str
    name: str
    group_id: str | None
    status: str
    created_at: datetime
    started_at: datetime | None
    started_by: str | None
    runs: list[InstallationRunOut]


class MarkDoneRequest(BaseModel):
    actor: str
    outcome: str = "success"
    detail: str | None = None


class ApprovalDecision(BaseModel):
    actor: str
    reason: str | None = None
