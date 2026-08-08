from datetime import datetime
from typing import Literal

from pydantic import BaseModel

LicenseComponentStatus = Literal["licensed", "demo", "unlicensed"]
InstanceStatus = Literal["active", "draining"]
SensorCost = Literal["cheap", "expensive"]


class SensorDeclaration(BaseModel):
    """Selbstdeklaration eines von einer Instanz angebotenen Sensors (10.1,
    P11-S1) - registry-service reicht das nur durch, `monitoring-service`
    aggregiert/loest die Aktivierung auf."""

    name: str
    group: str
    cost: SensorCost
    description: str


class RegisterRequest(BaseModel):
    instance_id: str
    service_type: str
    version: str
    capabilities: list[str] = []
    sensors: list[SensorDeclaration] = []
    health_endpoint: str
    address: str


class InstanceOut(BaseModel):
    instance_id: str
    service_type: str
    version: str
    capabilities: list[str]
    sensors: list[SensorDeclaration] = []
    health_endpoint: str
    address: str
    registered_at: datetime
    last_heartbeat_at: datetime
    healthy: bool
    # Lizenzvermittlung (Konzept 3.2b/9.3, P9-S2): wird von main.py nach dem
    # Aufbau aus `repository` per `ComponentLicenseCache` nachgetragen, nicht
    # persistiert - kein Feld auf `ServiceInstance`.
    license_status: LicenseComponentStatus = "licensed"
    # Drain-Mechanismus (10.5/3.8, P10-S2) - siehe models.ServiceInstance.status.
    status: InstanceStatus = "active"

    model_config = {"from_attributes": True}


class LicenseStatusForServiceOut(BaseModel):
    service_type: str
    status: LicenseComponentStatus
