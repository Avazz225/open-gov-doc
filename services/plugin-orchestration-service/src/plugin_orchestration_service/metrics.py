from dms_metrics_client import GuardedGauge, SensorRegistry, SensorSpec, http_sensor_declarations

# Sensor concept (10.1, Phase 40 Session 4) - migrates this service's
# `psutil`-sampled node resource data onto the real sensor infrastructure
# (previously only the generic HTTP ones, see settings.py's now-outdated
# "vollwertige Sensor-Infrastruktur ... erst Phase 11" comment). NOT a
# replacement for `sampler.py`'s existing `ClusterNode` upsert - that data
# feeds `placement.py`'s scheduling algorithm directly (a queryable DB
# row it needs for real decisions), whereas a `GuardedGauge` is write-
# only from the service's own perspective (no getter, only externally
# readable via a `/metrics` scrape) and could never serve that purpose.
# These two gauges are therefore ADDITIVE - fed from the SAME sampled
# values `sampler.run_tick` already computes for the `ClusterNode` upsert
# (see there), not a second independent sampling.
CPU_USAGE_PERCENT = SensorSpec(
    name="plugin_orchestration.node.cpu_usage_percent",
    group="capacity",
    cost="cheap",
    description="Aktuelle CPU-Auslastung des eigenen Knotens in Prozent (psutil, 3.8)",
)
AVAILABLE_RAM_MB = SensorSpec(
    name="plugin_orchestration.node.available_ram_mb",
    group="capacity",
    cost="cheap",
    description="Verfuegbarer Arbeitsspeicher des eigenen Knotens in MB (psutil, 3.8)",
)


def sensor_declarations() -> list[dict]:
    return [CPU_USAGE_PERCENT.as_dict(), AVAILABLE_RAM_MB.as_dict(), *http_sensor_declarations()]


def build_sensor_registry(registry: SensorRegistry) -> tuple[GuardedGauge, GuardedGauge]:
    cpu_usage_gauge = registry.gauge(CPU_USAGE_PERCENT)
    available_ram_gauge = registry.gauge(AVAILABLE_RAM_MB)
    return cpu_usage_gauge, available_ram_gauge
