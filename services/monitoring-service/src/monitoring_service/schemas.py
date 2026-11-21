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
    # P88-S2 (ADR 0243): which setting decided `active` - the sensor's own
    # override, its group's, its cost class's, or the global default.
    source: Literal["sensor", "group", "cost", "global"] = "global"


class SensorConfigOut(BaseModel):
    global_default: bool
    overrides: dict[str, bool]


class GlobalSensorConfigIn(BaseModel):
    enabled: bool


class SensorOverrideIn(BaseModel):
    """`{sensor_name}` in the path is a sensor name, `group:<group>` or
    `cost:<cheap|expensive>` (P88-S2, ADR 0243)."""

    # `None` deletes an existing override again - the sensor then
    # falls back to the global base setting.
    enabled: bool | None
