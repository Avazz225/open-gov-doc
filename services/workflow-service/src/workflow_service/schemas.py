from datetime import datetime

from pydantic import BaseModel


class ProcessDefinitionOut(BaseModel):
    id: int
    name: str
    bpmn_process_id: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ProcessDefinitionDetailOut(ProcessDefinitionOut):
    bpmn_xml: str


class ProcessInstanceCreate(BaseModel):
    created_by: str
    business_key: str | None = None
    initial_data: dict = {}


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
    # Camunda-`extensionElements`-Properties (3.10, P6-S7) - insbesondere
    # `taskType=signature`/`requiredLevel=...` bei einem Signature Task, siehe
    # spiff_adapter.py. Leer bei jedem gewöhnlichen Manual Task.
    extensions: dict[str, str] = {}


class TaskCompleteRequest(BaseModel):
    completed_by: str
    data: dict = {}
    # Pflicht, wenn die Task laut `extensions["taskType"] == "signature"`
    # markiert ist (3.10) - verweist auf eine zuvor beim Signature Service
    # erzeugte Signatur, siehe main.py._require_valid_signature_if_needed.
    signature_id: str | None = None
