from datetime import datetime

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
    # Optional: der Aufrufer bestimmt seine eigene Instanz-ID statt eine vom
    # Server generierte entgegenzunehmen (P12-S2, gleiches Muster wie
    # federation-hub-service's `handover_id`, ADR 0028) - wichtig, wenn der
    # allererste automatische Schritt fehlschlagen kann: ohne eine im Voraus
    # bekannte ID hätte der Aufrufer bei einem Fehlschlag von
    # `POST .../instances` selbst keine Möglichkeit, die (dennoch bereits
    # angelegte, siehe repository.start_instance) Instanz für einen späteren
    # `/retry`-Aufruf wiederzufinden.
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
    # Camunda-`extensionElements`-Properties (3.10, P6-S7) - insbesondere
    # `taskType=signature`/`requiredLevel=...` bei einem Signature Task, siehe
    # spiff_adapter.py. Leer bei jedem gewöhnlichen Manual Task.
    extensions: dict[str, str] = {}


class ReadyTaskWithInstanceOut(ReadyTaskOut):
    """`GET /tasks` (8, P14-S2) - dieselbe Task-Information wie `ReadyTaskOut`,
    zusätzlich um den instanzbezogenen Kontext ergänzt, der bei einer
    Cross-Instanz-Liste erst sichtbar gemacht werden muss (bei
    `GET /instances/{id}/tasks` ist die Instanz bereits durch die URL
    bekannt)."""

    instance_id: str
    process_definition_id: int
    business_key: str | None


class TaskCompleteRequest(BaseModel):
    completed_by: str
    data: dict = {}
    # Pflicht, wenn die Task laut `extensions["taskType"] == "signature"`
    # markiert ist (3.10) - verweist auf eine zuvor beim Signature Service
    # erzeugte Signatur, siehe main.py._require_valid_signature_if_needed.
    signature_id: str | None = None


class FederationConfigOut(BaseModel):
    version: str
    min_compatible_peer_version: str


class FederationConfigUpdate(BaseModel):
    version: str
    min_compatible_peer_version: str
