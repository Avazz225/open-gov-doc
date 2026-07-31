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


class TaskCompleteRequest(BaseModel):
    completed_by: str
    data: dict = {}
