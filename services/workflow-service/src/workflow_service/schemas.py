from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ProcessDefinitionOut(BaseModel):
    id: int
    name: str
    version: int
    bpmn_process_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProcessDefinitionDetailOut(ProcessDefinitionOut):
    bpmn_xml: str


class ProcessDefinitionImportResult(BaseModel):
    """Like `config_service.schemas.ImportActionResult`/`document_service`'s
    `ForceReleaseResult` - since Post-Roadmap Phase 21 Session 4 (ADR 0087),
    `POST /process-definitions` can optionally be gated via the generic
    four-eyes mechanism (`workflow.process_definition.import`)."""

    status: Literal["applied", "pending_approval"]
    result: ProcessDefinitionOut | None = None
    approval_request_id: str | None = None


class DmnDefinitionOut(BaseModel):
    id: int
    name: str
    version: int
    decision_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DmnDefinitionDetailOut(DmnDefinitionOut):
    dmn_xml: str


class ProcessInstanceCreate(BaseModel):
    created_by: str
    business_key: str | None = None
    initial_data: dict = {}
    # Optional: the caller determines its own instance ID instead of
    # accepting a server-generated one (P12-S2, same pattern as
    # federation-hub-service's `handover_id`, ADR 0028) - important when the
    # very first automatic step can fail: without an ID known in advance,
    # the caller would have no way, on a failure of `POST .../instances`
    # itself, to find the instance again (which was nonetheless already
    # created, see repository.start_instance) for a later `/retry` call.
    instance_id: str | None = None


class ProcessInstanceOut(BaseModel):
    id: str
    process_definition_id: int
    business_key: str | None
    status: str
    created_by: str
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class ReadyTaskOut(BaseModel):
    id: str
    name: str
    lane: str | None
    data: dict
    # Camunda `extensionElements` properties (3.10, P6-S7) - in particular
    # `taskType=signature`/`requiredLevel=...` for a Signature Task, see
    # spiff_adapter.py. Empty for every ordinary Manual Task.
    extensions: dict[str, str] = {}


class ReadyTaskWithInstanceOut(ReadyTaskOut):
    """`GET /tasks` (8, P14-S2) - the same task information as `ReadyTaskOut`,
    additionally augmented with the instance-related context that first
    needs to be made visible in a cross-instance list (with
    `GET /instances/{id}/tasks` the instance is already known via the
    URL)."""

    instance_id: str
    process_definition_id: int
    business_key: str | None


class TaskCompleteRequest(BaseModel):
    completed_by: str
    data: dict = {}
    # Required if the task is marked via `extensions["taskType"] == "signature"`
    # (3.10) - references a signature previously created at the Signature
    # Service, see main.py._require_valid_signature_if_needed.
    signature_id: str | None = None
    # Deputizing during absence (4.4a, P14-S11): set when this task is
    # completed on behalf of an absent person - the person actually acting
    # remains the caller reported via `X-DMS-Principal` (not `completed_by`,
    # which remains an unvalidated free-text field, see
    # main.py.complete_task), NOT this field - `on_behalf_of_principal_id`
    # is only the person being represented.
    on_behalf_of_principal_id: str | None = None


class FederationConfigOut(BaseModel):
    version: str
    min_compatible_peer_version: str


class FederationConfigUpdate(BaseModel):
    version: str
    min_compatible_peer_version: str


class BusinessCalendarOut(BaseModel):
    id: int
    name: str
    non_working_dates: list[str]
    is_default: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BusinessCalendarCreate(BaseModel):
    name: str
    non_working_dates: list[str] = []
    is_default: bool = False


class BusinessCalendarUpdate(BaseModel):
    name: str
    non_working_dates: list[str] = []
    is_default: bool = False
