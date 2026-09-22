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


class BrandingConfigOut(BaseModel):
    """Installation-level branding (7.3/8, P69-S2, ADR 0201) - `None` for
    any field means "use this build's static default," not "no branding
    configured." Deliberately ungated on read (see `get_branding_config`'s
    own docstring) - a frontend must be able to fetch this before login."""

    product_name: str | None = None
    accent_color: str | None = None
    logo_url: str | None = None
    updated_at: datetime

    model_config = {"from_attributes": True}


class BrandingConfigUpdate(BaseModel):
    product_name: str | None = None
    accent_color: str | None = None
    logo_url: str | None = None
