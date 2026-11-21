from typing import Literal

from pydantic import BaseModel


class SensorOut(BaseModel):
    """An entry of the sensor registry (10.1) - aggregated from the
    self-declarations of all instances currently known to `registry-service`,
    enriched with the resolved activation status."""

    name: str
    group: str
    # 1 (very cheap) to 5 (very expensive), P88-S2c; registrations from
    # older service images that still say cheap/expensive are normalized.
    cost: int
    description: str
    service_types: list[str]
    active: bool
    # P88-S2c: a sensor declared off by default stays off under "monitor
    # everything" until an override switches it on.
    default_enabled: bool = True
    # P88-S2 (ADR 0243): which setting decided `active` - the sensor's own
    # override, its group's, its cost class's, or the global default.
    source: Literal["sensor", "group", "cost", "global", "default"] = "global"


class SensorConfigOut(BaseModel):
    global_default: bool
    overrides: dict[str, bool]


class GlobalSensorConfigIn(BaseModel):
    enabled: bool


class SensorOverrideIn(BaseModel):
    """`{sensor_name}` in the path is a sensor name, `group:<group>` or
    `cost:<1-5>` (P88-S2/S2c, ADR 0243). The legacy `cost:cheap`/
    `cost:expensive` are accepted and written as levels 1-3/4-5."""

    # `None` deletes an existing override again - the sensor then
    # falls back to the global base setting.
    enabled: bool | None
