from typing import Literal

from pydantic import BaseModel

SensorCost = Literal["cheap", "expensive"]


class SensorOut(BaseModel):
    """Ein Eintrag der Sensor-Registry (10.1) - aggregiert aus den
    Selbstdeklarationen aller aktuell bei `registry-service` bekannten
    Instanzen, angereichert um den aufgelösten Aktivierungsstatus."""

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
    # `None` löscht einen bestehenden Override wieder - der Sensor fällt
    # dann zurück auf die globale Grundeinstellung.
    enabled: bool | None
