from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ScalingType = Literal["stateless_horizontal", "singleton"]
PlacementSource = Literal["manifest", "observed_median", "default_fallback"]
PlacementMethod = Literal["platform_scheduler", "ffd"]


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
    """Bounds since P62-S1: `placement.py`'s `median(report.cpu_cores ...)`
    consumes this value directly for the "observed_median" resource
    estimate - previously unbounded, so a single out-of-range report could
    permanently skew that estimate above every node's real capacity. Upper
    bounds are deliberately generous (far above any realistic single-plugin
    footprint), just enough to rule out an absurd outlier value, not to
    model a real capacity ceiling."""

    instance_id: str
    cpu_cores: float = Field(gt=0, le=1024)
    ram_mb: float = Field(gt=0, le=1_048_576)


class ClusterNodeIn(BaseModel):
    cpu_cores: float
    total_ram_mb: float
    cpu_usage_percent: float = 0.0
    available_ram_mb: float | None = None


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
    placement_method: PlacementMethod
    placement_allowed: bool
    reason: str | None
    dependency_status: dict[str, bool]
    decided_at: datetime

    model_config = {"from_attributes": True}
