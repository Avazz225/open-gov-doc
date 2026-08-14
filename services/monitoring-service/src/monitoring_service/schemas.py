from typing import Literal

from pydantic import BaseModel

SensorCost = Literal["cheap", "expensive"]


class SensorOut(BaseModel):
    """An entry of the sensor registry (10.1) - aggregated from the
    self-declarations of all instances currently known to `registry-service`,
    enriched with the resolved activation status."""

    name: str
    group: str
    cost: SensorCost
    description: str
    service_types: list[str]
    active: bool


class SensorConfigOut(BaseModel):
    global_default: bool
    overrides: dict[str, bool]


class GlobalSensorConfigIn(BaseModel):
    enabled: bool


class SensorOverrideIn(BaseModel):
    # `None` deletes an existing override again - the sensor then
    # falls back to the global base setting.
    enabled: bool | None
