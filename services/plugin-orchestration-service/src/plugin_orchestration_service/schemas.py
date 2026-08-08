from datetime import datetime
from typing import Literal

from pydantic import BaseModel

ScalingType = Literal["stateless_horizontal", "singleton"]
PlacementSource = Literal["manifest", "observed_median", "default_fallback"]


class PluginManifestIn(BaseModel):
    version: str
    scaling_type: ScalingType
    resource_cpu_cores: float | None = None
    resource_ram_mb: float | None = None
    load_profile: str | None = None
    dependencies: list[str] = []


class PluginManifestOut(BaseModel):
    plugin_type: str
    version: str
    scaling_type: ScalingType
    resource_cpu_cores: float | None
    resource_ram_mb: float | None
    load_profile: str | None
    dependencies: list[str]
    registered_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ResourceUsageReportIn(BaseModel):
    instance_id: str
    cpu_cores: float
    ram_mb: float


class ClusterNodeOut(BaseModel):
    node_id: str
    cpu_cores: float
    total_ram_mb: float
    cpu_usage_percent: float
    available_ram_mb: float
    sampled_at: datetime

    model_config = {"from_attributes": True}


class PlacementRequestIn(BaseModel):
    plugin_type: str


class PlacementDecisionOut(BaseModel):
    id: int
    plugin_type: str
    node_id: str | None
    estimated_cpu_cores: float
    estimated_ram_mb: float
    source: PlacementSource
    placement_allowed: bool
    reason: str | None
    dependency_status: dict[str, bool]
    decided_at: datetime

    model_config = {"from_attributes": True}
