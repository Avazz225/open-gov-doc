from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

SensorCost = Literal["cheap", "expensive"]


@dataclass(frozen=True)
class SensorSpec:
    """Ein einzelner, klar benannter Messpunkt (10.1) - kennt seine eigene
    Gruppe (fachliche Einordnung, z. B. für eine künftige Admin-UI-Übersicht)
    und seine Kosten (CPU/IO-Overhead), damit ein "alles an" nicht
    versehentlich das System spürbar belastet."""

    name: str
    group: str
    cost: SensorCost
    description: str

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "group": self.group,
            "cost": self.cost,
            "description": self.description,
        }


IsActiveFn = Callable[[str], bool]


class GuardedCounter:
    """Wrapper um einen `prometheus_client.Counter` - `inc()` fasst das
    zugrunde liegende Metrik-Objekt NICHT an, wenn der Sensor deaktiviert
    ist (10.1: "kein Erzeugen, kein Zwischenspeichern" bei Deaktivierung,
    nicht nur ein Verstecken im Export)."""

    def __init__(self, name: str, metric: Counter, is_active: IsActiveFn) -> None:
        self._name = name
        self._metric = metric
        self._is_active = is_active

    def is_active(self) -> bool:
        return self._is_active(self._name)

    def inc(self, amount: float = 1.0) -> None:
        if self.is_active():
            self._metric.inc(amount)


class GuardedGauge:
    def __init__(self, name: str, metric: Gauge, is_active: IsActiveFn) -> None:
        self._name = name
        self._metric = metric
        self._is_active = is_active

    def is_active(self) -> bool:
        """Erlaubt Aufrufern (z. B. `loop.run_gauge_sampler_loop`), eine ggf.
        teure Datenbankabfrage für den aktuellen Wert gar nicht erst
        auszuführen, wenn der Sensor deaktiviert ist."""
        return self._is_active(self._name)

    def set(self, value: float) -> None:
        if self.is_active():
            self._metric.set(value)


class GuardedHistogram:
    def __init__(self, name: str, metric: Histogram, is_active: IsActiveFn) -> None:
        self._name = name
        self._metric = metric
        self._is_active = is_active

    def is_active(self) -> bool:
        """Erlaubt Aufrufern, eine Zeitmessung selbst gar nicht erst zu
        starten, wenn der Sensor deaktiviert ist (z. B. `time.monotonic()`
        vor einem Upload) - auch dieser minimale Overhead unterbleibt dann."""
        return self._is_active(self._name)

    def observe(self, value: float) -> None:
        if self.is_active():
            self._metric.observe(value)


class SensorRegistry:
    """Laufzeit-Registrierung der von einem Service angebotenen Sensoren
    (10.1) - hält die deklarierten `SensorSpec`s (für die Selbstregistrierung
    bei `registry-service`, siehe `dms_registry_client`) und baut daraus
    `Guarded*`-Prometheus-Objekte in einer eigenen `CollectorRegistry`
    (nicht der globale Default - vermeidet Testkontamination über mehrere
    Service-Instanzen im selben Prozess hinweg)."""

    def __init__(self, service_name: str, *, is_active: IsActiveFn) -> None:
        self.service_name = service_name
        self.collector_registry = CollectorRegistry()
        self._is_active = is_active
        self._specs: dict[str, SensorSpec] = {}

    def specs(self) -> list[SensorSpec]:
        return list(self._specs.values())

    def _register_spec(self, spec: SensorSpec) -> None:
        if spec.name in self._specs:
            raise ValueError(f"Sensor {spec.name!r} bereits registriert")
        self._specs[spec.name] = spec

    @staticmethod
    def _prom_name(name: str) -> str:
        return name.replace(".", "_").replace("-", "_")

    def counter(self, spec: SensorSpec) -> GuardedCounter:
        self._register_spec(spec)
        metric = Counter(
            self._prom_name(spec.name), spec.description, registry=self.collector_registry
        )
        return GuardedCounter(spec.name, metric, self._is_active)

    def gauge(self, spec: SensorSpec) -> GuardedGauge:
        self._register_spec(spec)
        metric = Gauge(
            self._prom_name(spec.name), spec.description, registry=self.collector_registry
        )
        return GuardedGauge(spec.name, metric, self._is_active)

    def histogram(self, spec: SensorSpec) -> GuardedHistogram:
        self._register_spec(spec)
        metric = Histogram(
            self._prom_name(spec.name), spec.description, registry=self.collector_registry
        )
        return GuardedHistogram(spec.name, metric, self._is_active)
