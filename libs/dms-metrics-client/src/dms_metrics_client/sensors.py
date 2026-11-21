from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily

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

logger = logging.getLogger(__name__)

# What a scrape sampler returns (P88-S2b): one value for an unlabelled gauge,
# a mapping from label tuples to values for a labelled one, or None when
# there is nothing to report this time (the gauge keeps its last value).
SampleValue = float | dict[tuple[str, ...], float] | None


def _accepts_group_and_cost(fn: Callable[..., bool]) -> bool:
    """`SensorConfigClient`/`SensorConfigProxy.is_active` take the sensor's
    group and cost as keywords (P88-S2); a plain `lambda name: ...` (tests,
    simple callers) doesn't, and keeps working."""
    try:
        parameters = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return "group" in parameters and "cost" in parameters


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


class _OptionalGauge:
    """An unlabelled gauge that is absent from the exposition until it has a
    value, and again after `clear()` (P88-S2b). A plain Gauge exports 0
    before its first `set`, which reads as a real value - "0 days until the
    license expires" when no license is installed at all."""

    _labelnames: tuple[str, ...] = ()

    def __init__(self, name: str, documentation: str, registry: CollectorRegistry) -> None:
        self._name = name
        self._documentation = documentation
        self._value: float | None = None
        registry.register(self)

    def set(self, value: float) -> None:
        self._value = float(value)

    def inc(self, amount: float = 1.0) -> None:
        self._value = (self._value or 0.0) + amount

    def clear(self) -> None:
        self._value = None

    def collect(self):
        if self._value is None:
            return []
        return [GaugeMetricFamily(self._name, self._documentation, value=self._value)]


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

    def set(self, value: float, **labels: str) -> None:
        """Optional `**labels` (P87-S2: registry-service's per-type replica
        gauges) work as on `GuardedCounter.inc`; unlabeled gauges pass none."""
        if self.is_active():
            target = self._metric.labels(**labels) if labels else self._metric
            target.set(value)

    def clear(self) -> None:
        """Drops every label set of a labelled gauge, e.g. for a service
        type that is no longer reported; unsets an optional gauge. A no-op
        for other unlabeled gauges."""
        if self._metric._labelnames or isinstance(self._metric, _OptionalGauge):
            self._metric.clear()

    def add(self, amount: float, *, checked: bool = True) -> None:
        """Increments/decrements an unlabelled gauge (P88-S2b: requests in
        flight). `checked=False` skips the activation check - for the
        decrement that must match an increment made while the sensor was
        still active, so switching it off mid-request can't skew the
        value."""
        if not checked or self.is_active():
            self._metric.inc(amount)

    def labelnames(self) -> tuple[str, ...]:
        return tuple(self._metric._labelnames)

    def apply(self, value: SampleValue) -> None:
        """Writes a sampler's result (see `SampleValue`). `None` keeps the
        last value - except on an optional gauge, which it removes."""
        if value is None:
            if isinstance(self._metric, _OptionalGauge):
                self._metric.clear()
            return
        if isinstance(value, dict):
            self.clear()
            names = self.labelnames()
            for label_values, item in value.items():
                self.set(float(item), **dict(zip(names, label_values, strict=True)))
        else:
            self.set(float(value))


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
        self._with_group_and_cost = _accepts_group_and_cost(is_active)
        self._specs: dict[str, SensorSpec] = {}
        self._sync_samplers: list[tuple[GuardedGauge, Callable[[], SampleValue]]] = []
        self._async_samplers: list[tuple[GuardedGauge, Callable[[], Awaitable[SampleValue]]]] = []

    # --- Pull-on-scrape samplers (P88-S2b) ---------------------------------
    # A gauge whose value is computed when `/metrics` is read, and only
    # while its sensor is active - no background loop, no cost when off
    # (10.1). Sync samplers run in `metrics_payload`; async ones (database
    # counts) need `refreshed_metrics_payload` in an async endpoint.

    def on_scrape(self, gauge: GuardedGauge, sampler: Callable[[], SampleValue]) -> None:
        self._sync_samplers.append((gauge, sampler))

    def on_scrape_async(
        self, gauge: GuardedGauge, sampler: Callable[[], Awaitable[SampleValue]]
    ) -> None:
        self._async_samplers.append((gauge, sampler))

    def run_sync_samplers(self) -> None:
        for gauge, sampler in self._sync_samplers:
            if not gauge.is_active():
                continue
            try:
                gauge.apply(sampler())
            except Exception:
                logger.exception("sensor_sample_failed", extra={"sensor": gauge._name})

    async def run_async_samplers(self) -> None:
        for gauge, sampler in self._async_samplers:
            if not gauge.is_active():
                continue
            try:
                gauge.apply(await sampler())
            except Exception:
                logger.exception("sensor_sample_failed", extra={"sensor": gauge._name})

    def _check_for(self, spec: SensorSpec) -> IsActiveFn:
        """The activation check one `Guarded*` sensor calls on every use -
        bound to its own group and cost, so a group or cost-class override
        reaches it (ADR 0243)."""
        if not self._with_group_and_cost:
            return self._is_active
        is_active = self._is_active

        def check(name: str) -> bool:
            return is_active(name, group=spec.group, cost=spec.cost)

        return check

    def specs(self) -> list[SensorSpec]:
        return list(self._specs.values())

    def declarations(self) -> list[dict]:
        """Everything registered here, for self-registration with
        registry-service (P88-S2b) - so a service's declared catalog can't
        drift from the sensors it actually built."""
        return [spec.as_dict() for spec in self._specs.values()]

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
        return GuardedCounter(spec.name, metric, self._check_for(spec))

    def gauge(
        self, spec: SensorSpec, *, labelnames: tuple[str, ...] = (), optional: bool = False
    ) -> GuardedGauge:
        """`optional=True` (unlabelled only): absent until it has a value,
        see `_OptionalGauge`."""
        self._register_spec(spec)
        if optional:
            if labelnames:
                raise ValueError("optional gauges are unlabelled")
            optional_metric = _OptionalGauge(
                self._prom_name(spec.name), spec.description, self.collector_registry
            )
            return GuardedGauge(spec.name, optional_metric, self._check_for(spec))
        metric = Gauge(
            self._prom_name(spec.name),
            spec.description,
            labelnames=labelnames,
            registry=self.collector_registry,
        )
        return GuardedGauge(spec.name, metric, self._check_for(spec))

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
        return GuardedHistogram(spec.name, metric, self._check_for(spec))
