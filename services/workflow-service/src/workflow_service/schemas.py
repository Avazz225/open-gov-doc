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
    # Task-claim mechanism (Post-Roadmap Phase 31 Session 10) - `None` if
    # unclaimed. Enriched by `main.py` from `TaskClaim` (a side table, not
    # part of the transient task itself, see `models.TaskClaim`).
    claimed_by: str | None = None
    grant_kind: str | None = None


class ReadyTaskWithInstanceOut(ReadyTaskOut):
    """`GET /tasks` (8, P14-S2) - the same task information as `ReadyTaskOut`,
    additionally augmented with the instance-related context that first
    needs to be made visible in a cross-instance list (with
    `GET /instances/{id}/tasks` the instance is already known via the
    URL)."""

    instance_id: str
    process_definition_id: int
    business_key: str | None
    # "Unclaimed team work" view (Post-Roadmap Phase 35 Session 3, ADR
    # 0145) - the only existing attribution signal an UNCLAIMED task has at
    # all (`claimed_by` is `None`). Reused, not new: `ProcessInstance.
    # created_by` already existed for org-unit resolution
    # (`org_unit_of="creator"`), just never surfaced on this endpoint.
    created_by: str


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


class TaskClaimCreate(BaseModel):
    principal_id: str


class TaskReassignRequest(BaseModel):
    """`POST .../tasks/{task_id}/reassign` (Post-Roadmap Phase 35 Session 3,
    ADR 0145) - like `TaskClaimCreate`, `new_principal_id` is an explicit
    field, not always `X-DMS-Principal`: a supervisor reassigning FROM one
    report TO another is acting on neither person's own behalf."""

    new_principal_id: str


class TaskClaimOut(BaseModel):
    id: int
    instance_id: str
    task_id: str
    principal_id: str
    claimed_at: datetime
    grant_kind: str | None
    granted_delegation_ids: list[str] | None

    model_config = {"from_attributes": True}


class OrgHierarchyGrantRequest(BaseModel):
    """Dynamic org-hierarchy-based temporary access grant (14.2,
    Post-Roadmap Phase 31 Session 10) - requires the task to already be
    claimed (`TaskClaim`), since the grant is always resolved FROM the
    claim's principal (`grant_kind="supervisor"`/`"supervisor_chain"`) or,
    for `grant_kind="org_unit"`, from either the claim's principal
    (assignee) or the process instance's `created_by` (creator) -
    `org_unit_of` picks which, required only for that grant kind."""

    grant_kind: Literal["supervisor", "supervisor_chain", "org_unit"]
    org_unit_of: Literal["assignee", "creator"] | None = None


class OrgHierarchyGrantResultOut(BaseModel):
    grant_kind: str
    deputy_principal_ids: list[str]


class FederationConfigOut(BaseModel):
    version: str
    min_compatible_peer_version: str


class FederationConfigUpdate(BaseModel):
    version: str
    min_compatible_peer_version: str


class XdomeaHandoffInboundResultOut(BaseModel):
    """Response shape for the reserved `xdomea.case_handoff` process type on
    `POST /federation/inbound` (Post-Roadmap Phase 43 Session 1, ADR
    0147/ADR 0159) - `status` plus every field of
    `archival_service.schemas.XdomeaImportResultOut` unchanged, since this
    is genuinely that same result, just relayed one hop further. `POST
    /federation/inbound` returns either this or `ProcessInstanceOut`,
    depending on whether `process_type` was the reserved value (no BPMN
    instance is started for it) or a mapped one. Only ever constructed on
    the success path - a failed import raises `HTTPException` instead."""

    status: str
    case_id: str | None
    case_created: bool
    vorgang_betreff: str | None
    document_ids: list[str]
    skipped_document_count: int
    skipped_schriftstueck_count: int


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
