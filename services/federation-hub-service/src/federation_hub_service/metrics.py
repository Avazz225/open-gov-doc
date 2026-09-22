from collections.abc import Awaitable, Callable

from dms_metrics_client import GuardedGauge, SensorRegistry, SensorSpec, http_sensor_declarations

from federation_hub_service import repository

# Sensor concept (10.1, Phase 40 Session 4) - this service's first custom
# sensors (previously no sensors at all, see settings.py). Names both
# "retry-cache depth"/"pending count" as the plan asked, directly against
# the two payload retry caches from ADR 0081/Phase 40 Session 3 - these are
# exactly the caches ADR 0147 flagged as a memory-pressure risk for large
# payloads, so their live size is the single most relevant number to
# expose. Since Post-Roadmap Phase 73 Session 3 (ADR 0213), both caches are
# persisted in `handover_retry_payload` rather than in-process dicts - the
# sensors themselves didn't need to change shape (still "live pending
# count per leg"), only how that count is obtained (see `build_samplers`
# below). This also makes the sensor a strictly MORE accurate number than
# before: the old docstring here explicitly declined to also expose the
# DB's `pending_retry`/`result_pending_retry` row counts as a second pair
# of sensors because they could diverge from the in-process cache size
# after a restart - now that the cache itself IS the DB, there is only one
# source of truth left, so that divergence risk (and the reason to keep
# them separate) is gone.
FORWARD_RETRY_CACHE_DEPTH = SensorSpec(
    name="federation_hub.retry_cache.forward_pending",
    group="capacity",
    cost="cheap",
    description=(
        "Anzahl persistent zwischengespeicherter, noch nicht erfolgreich "
        "zugestellter Handover-Payloads (Hinweg, ADR 0081/ADR 0213)"
    ),
)
RESULT_RETRY_CACHE_DEPTH = SensorSpec(
    name="federation_hub.retry_cache.result_pending",
    group="capacity",
    cost="cheap",
    description=(
        "Anzahl persistent zwischengespeicherter, noch nicht erfolgreich "
        "zugestellter Ergebnis-Payloads (Rückweg, Phase 40 Session 3/ADR 0213)"
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
    session_factory,
) -> dict[str, tuple[GuardedGauge, Callable[[], Awaitable[float]]]]:
    """Since Post-Roadmap Phase 73 Session 3 (ADR 0213), both caches are
    persisted in `handover_retry_payload` rather than plain in-process
    dicts - each sampler now opens its own short-lived session and runs a
    cheap `COUNT(*) ... WHERE leg = ...` via `repository.
    count_pending_payloads` instead of a `len(dict)`. Genuine DB/network
    access now, unlike before - still `async def` to match
    `run_gauge_sampler_loop`'s expected signature, same idiom as every other
    service's `build_samplers`, but no longer merely a formality here."""

    async def forward_depth() -> float:
        async with session_factory() as session:
            return float(await repository.count_pending_payloads(session, leg="forward"))

    async def result_depth() -> float:
        async with session_factory() as session:
            return float(await repository.count_pending_payloads(session, leg="result"))

    return {
        FORWARD_RETRY_CACHE_DEPTH.name: (forward_gauge, forward_depth),
        RESULT_RETRY_CACHE_DEPTH.name: (result_gauge, result_depth),
    }
