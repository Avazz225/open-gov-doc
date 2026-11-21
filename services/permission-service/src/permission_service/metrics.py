"""permission-service sensors (P88-S2b, package D): how the effective-
permission cache behaves - lookups by hit/miss, invalidations, and how long
recomputing an entry takes (the timing histogram is the expensive one)."""

from __future__ import annotations

from dms_metrics_client import GuardedCounter, GuardedHistogram, SensorRegistry, SensorSpec

LOOKUPS = SensorSpec(
    name="permission.cache.lookups",
    group="security",
    cost="cheap",
    description="Abfragen des Rechte-Caches je Ergebnis (hit, miss)",
)
INVALIDATIONS = SensorSpec(
    name="permission.cache.invalidations",
    group="security",
    cost="cheap",
    description="Komplette Invalidierungen des Rechte-Caches (jede Rechteaenderung)",
)
REBUILD_DURATION = SensorSpec(
    name="permission.cache.rebuild.duration_seconds",
    group="performance",
    cost="expensive",
    description="Dauer, einen Cache-Eintrag neu zu berechnen (Rollen ueber den Ressourcenbaum)",
)

_lookups: GuardedCounter | None = None
_invalidations: GuardedCounter | None = None
_rebuild: GuardedHistogram | None = None


def build_sensors(registry: SensorRegistry, app=None) -> None:
    global _lookups, _invalidations, _rebuild
    _lookups = registry.counter(LOOKUPS, labelnames=("result",))
    _invalidations = registry.counter(INVALIDATIONS)
    _rebuild = registry.histogram(REBUILD_DURATION)


def count_lookup(hit: bool) -> None:
    if _lookups is not None:
        _lookups.inc(result="hit" if hit else "miss")


def count_invalidation() -> None:
    if _invalidations is not None:
        _invalidations.inc()


def rebuild_timing_active() -> bool:
    return _rebuild is not None and _rebuild.is_active()


def observe_rebuild(seconds: float) -> None:
    if _rebuild is not None:
        _rebuild.observe(seconds)
