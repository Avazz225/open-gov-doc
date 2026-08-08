from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LicenseComponentStatus = Literal["licensed", "demo", "unlicensed"]


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
    # Lizenzvermittlung (Konzept 3.2b/9.3, P9-S2): wird von main.py nach dem
    # Aufbau aus `repository` per `ComponentLicenseCache` nachgetragen, nicht
    # persistiert - kein Feld auf `ServiceInstance`.
    license_status: LicenseComponentStatus = "licensed"

    model_config = {"from_attributes": True}


class LicenseStatusForServiceOut(BaseModel):
    service_type: str
    status: LicenseComponentStatus
