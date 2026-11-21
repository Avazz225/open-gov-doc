from datetime import datetime
from typing import Literal

from dms_metrics_client import normalize_cost
from pydantic import BaseModel, Field, field_validator, model_validator

LicenseComponentStatus = Literal["licensed", "demo", "unlicensed"]
InstanceStatus = Literal["active", "draining"]


class SensorDeclaration(BaseModel):
    """Selbstdeklaration eines von einer Instanz angebotenen Sensors (10.1,
    P11-S1) - registry-service reicht das nur durch, `monitoring-service`
    aggregiert/loest die Aktivierung auf.

    P88-S2c: `cost` is a level 1-5; an older instance still sending
    "cheap"/"expensive" is accepted and stored as 2/4, so a mixed fleet
    during a rolling update keeps registering. `default_enabled` is new and
    defaults to on for instances that don't send it."""

    name: str
    group: str
    cost: int
    description: str
    default_enabled: bool = True

    @field_validator("cost", mode="before")
    @classmethod
    def _normalize_cost(cls, value: int | str) -> int:
        return normalize_cost(value)


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


ExpectationSource = Literal["admin", "default", "none"]
ReplicaState = Literal["ok", "under", "vanished", "over", "unmonitored"]


class ExpectedReplicasUpdate(BaseModel):
    """`PUT /replicas/{service_type}` (P87-S2, ADR 0241 Decision 3) - the
    admin override. `max_replicas` None means "no upper limit"."""

    min_replicas: int = Field(ge=1)
    max_replicas: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _max_not_below_min(self) -> "ExpectedReplicasUpdate":
        if self.max_replicas is not None and self.max_replicas < self.min_replicas:
            raise ValueError("max_replicas darf nicht kleiner als min_replicas sein")
        return self


class ReplicaTypeOut(BaseModel):
    """One row of `GET /replicas` (P87-S2, ADR 0241): the expectation and
    where it comes from, the live counts, and the committed alarm state
    with `since` (plus a pending state still inside its debounce window)."""

    service_type: str
    expected_min: int | None
    expected_max: int | None
    source: ExpectationSource
    reachable: int
    draining: int
    unreachable: int
    state: ReplicaState
    since: datetime | None = None
    pending_state: ReplicaState | None = None


class ReplicaSummaryOut(BaseModel):
    """`GET /replicas/summary` for fleet-management (P87-S3, ADR 0241
    Decision 5): how many types are in each state, plus the types in an
    alarm state - no instance ids or addresses leave the installation."""

    counts: dict[str, int]
    alarms: list[ReplicaTypeOut]
