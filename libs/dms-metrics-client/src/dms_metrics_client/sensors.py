from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

SensorCost = Literal["cheap", "expensive"]


@dataclass(frozen=True)
class SensorSpec:
    """A single, clearly named measurement point (10.1) - knows its own
    group (business categorization, e.g. for a future admin UI overview)
    and its cost (CPU/IO overhead), so an "everything on" setting doesn't
    accidentally place a noticeable load on the system."""

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
    """Wrapper around a `prometheus_client.Counter` - `inc()` does NOT touch
    the underlying metric object when the sensor is deactivated (10.1: "no
    generation, no buffering" when deactivated, not just hiding it from the
    export). Optional `**labels` (e.g. for `http.requests`'s
    method/route/status dimensions) are applied via the wrapped metric's own
    `.labels(...)` - unlabeled sensors simply pass none."""

    def __init__(self, name: str, metric: Counter, is_active: IsActiveFn) -> None:
        self._name = name
        self._metric = metric
        self._is_active = is_active

    def is_active(self) -> bool:
        return self._is_active(self._name)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if self.is_active():
            target = self._metric.labels(**labels) if labels else self._metric
            target.inc(amount)


class GuardedGauge:
    def __init__(self, name: str, metric: Gauge, is_active: IsActiveFn) -> None:
        self._name = name
        self._metric = metric
        self._is_active = is_active

    def is_active(self) -> bool:
        """Allows callers (e.g. `loop.run_gauge_sampler_loop`) to avoid even
        running a possibly expensive database query for the current value
        when the sensor is deactivated."""
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
        """Allows callers to avoid even starting a time measurement when the
        sensor is deactivated (e.g. `time.monotonic()` before an upload) -
        this minimal overhead is then also avoided."""
        return self._is_active(self._name)

    def observe(self, value: float, **labels: str) -> None:
        if self.is_active():
            target = self._metric.labels(**labels) if labels else self._metric
            target.observe(value)


class SensorRegistry:
    """Runtime registration of the sensors offered by a service (10.1) -
    holds the declared `SensorSpec`s (for self-registration with
    `registry-service`, see `dms_registry_client`) and builds `Guarded*`
    Prometheus objects from them in its own `CollectorRegistry` (not the
    global default - avoids test contamination across multiple service
    instances in the same process)."""

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

    def counter(self, spec: SensorSpec, *, labelnames: tuple[str, ...] = ()) -> GuardedCounter:
        self._register_spec(spec)
        metric = Counter(
            self._prom_name(spec.name),
            spec.description,
            labelnames=labelnames,
            registry=self.collector_registry,
        )
        return GuardedCounter(spec.name, metric, self._is_active)

    def gauge(self, spec: SensorSpec) -> GuardedGauge:
        self._register_spec(spec)
        metric = Gauge(
            self._prom_name(spec.name), spec.description, registry=self.collector_registry
        )
        return GuardedGauge(spec.name, metric, self._is_active)

    def histogram(
        self,
        spec: SensorSpec,
        *,
        labelnames: tuple[str, ...] = (),
        buckets: tuple[float, ...] | None = None,
    ) -> GuardedHistogram:
        self._register_spec(spec)
        kwargs = {} if buckets is None else {"buckets": buckets}
        metric = Histogram(
            self._prom_name(spec.name),
            spec.description,
            labelnames=labelnames,
            registry=self.collector_registry,
            **kwargs,
        )
        return GuardedHistogram(spec.name, metric, self._is_active)
