from datetime import datetime

from pydantic import BaseModel


class RegisterRequest(BaseModel):
    instance_id: str
    service_type: str
    version: str
    capabilities: list[str] = []
    health_endpoint: str
    address: str


class InstanceOut(BaseModel):
    instance_id: str
    service_type: str
    version: str
    capabilities: list[str]
    health_endpoint: str
    address: str
    registered_at: datetime
    last_heartbeat_at: datetime
    healthy: bool

    model_config = {"from_attributes": True}
