from collections.abc import Awaitable, Callable

from dms_metrics_client import GuardedGauge, SensorRegistry, SensorSpec, http_sensor_declarations

# Sensor concept (10.1, Phase 40 Session 4) - this service's first custom
# sensors (previously no sensors at all, see settings.py). Names both
# "retry-cache depth"/"pending count" as the plan asked, directly against
# the two in-process, ephemeral payload caches from ADR 0081/Phase 40
# Session 3 (`app.state.pending_handover_payloads`/
# `..._result_payloads`) - these are exactly the caches ADR 0147 flagged
# as a memory-pressure risk for large payloads, so their live size is the
# single most relevant number to expose. Deliberately does NOT also
# expose the DB's `pending_retry`/`result_pending_retry` row counts as a
# separate pair of sensors - those can diverge from the cache size after a
# restart (documented, deliberate limitation, see `docs/services/
# federation-hub-service.md`), but adding four sensors for two caches
# would exceed this session's own "two concrete sensors, not a blanket
# retrofit" scope; the cache size is what actually drives the memory-
# pressure risk this session is about.
FORWARD_RETRY_CACHE_DEPTH = SensorSpec(
    name="federation_hub.retry_cache.forward_pending",
    group="capacity",
    cost="cheap",
    description=(
        "Anzahl im Hub-Prozessspeicher zwischengespeicherter, noch nicht "
        "erfolgreich zugestellter Handover-Payloads (Hinweg, ADR 0081)"
    ),
)
RESULT_RETRY_CACHE_DEPTH = SensorSpec(
    name="federation_hub.retry_cache.result_pending",
    group="capacity",
    cost="cheap",
    description=(
        "Anzahl im Hub-Prozessspeicher zwischengespeicherter, noch nicht "
        "erfolgreich zugestellter Ergebnis-Payloads (Rückweg, Phase 40 "
        "Session 3)"
    ),
)


def sensor_declarations() -> list[dict]:
    return [
        FORWARD_RETRY_CACHE_DEPTH.as_dict(),
        RESULT_RETRY_CACHE_DEPTH.as_dict(),
        *http_sensor_declarations(),
    ]


def build_sensor_registry(registry: SensorRegistry) -> tuple[GuardedGauge, GuardedGauge]:
    forward_gauge = registry.gauge(FORWARD_RETRY_CACHE_DEPTH)
    result_gauge = registry.gauge(RESULT_RETRY_CACHE_DEPTH)
    return forward_gauge, result_gauge


def build_samplers(
    forward_gauge: GuardedGauge,
    result_gauge: GuardedGauge,
    pending_payloads: dict[str, dict],
    pending_result_payloads: dict[str, dict],
) -> dict[str, tuple[GuardedGauge, Callable[[], Awaitable[float]]]]:
    """Both caches are plain in-process dicts (no DB/network access needed
    to read them) - the sampler functions are still `async def` only to
    match `run_gauge_sampler_loop`'s expected signature, same idiom as
    every other service's `build_samplers`."""

    async def forward_depth() -> float:
        return float(len(pending_payloads))

    async def result_depth() -> float:
        return float(len(pending_result_payloads))

    return {
        FORWARD_RETRY_CACHE_DEPTH.name: (forward_gauge, forward_depth),
        RESULT_RETRY_CACHE_DEPTH.name: (result_gauge, result_depth),
    }
