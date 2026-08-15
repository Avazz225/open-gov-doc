from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
from monitoring_service.clients import RegistryInstance
from prometheus_client.metrics_core import Metric
from prometheus_client.parser import text_string_to_metric_families
from prometheus_client.samples import Sample


def merge_metric_families(bodies: dict[str, str], service_types: dict[str, str]) -> list[Metric]:
    """Merges the raw scraped exposition texts of multiple instances into a
    list of metric families - a real proxy/federation pattern instead of
    text concatenation: mere concatenation would violate the exposition
    format's grouping rule for same-named metrics across multiple
    instances (HELP/TYPE must be contiguous). Each sample gets an
    additional `instance` label for disambiguation, plus a `service` label
    (from `service_types[instance_id]`, i.e. `RegistryInstance.service_type`)
    - `instance` alone is an opaque UUID, `service` is what per-service
    dashboards (10.1 full rollout) actually group/filter by."""
    merged: dict[str, Metric] = {}
    for instance_id, body in bodies.items():
        for family in text_string_to_metric_families(body):
            target = merged.get(family.name)
            if target is None:
                target = Metric(family.name, family.documentation, family.type)
                merged[family.name] = target
            for sample in family.samples:
                labels = {
                    **sample.labels,
                    "instance": instance_id,
                    "service": service_types.get(instance_id, "unknown"),
                }
                target.samples.append(
                    Sample(sample.name, labels, sample.value, sample.timestamp, sample.exemplar)
                )
    return list(merged.values())


async def scrape_targets(
    client: httpx.AsyncClient,
    instances: list[RegistryInstance],
    *,
    timeout_seconds: float,
    on_failure: Callable[[], None],
) -> dict[str, str]:
    """Fetches `/metrics` of all given instances in parallel. A
    failing target does not block the others (`asyncio.gather` without
    propagating `return_exceptions` outward) and increments `on_failure()`
    (the always-active `monitoring.scrape.failures_total` counter, see
    `main.py` - deliberately outside the configurable sensor catalog)."""

    async def fetch(instance: RegistryInstance) -> tuple[str, str] | None:
        try:
            response = await client.get(f"{instance.address}/metrics", timeout=timeout_seconds)
            response.raise_for_status()
            return instance.instance_id, response.text
        except httpx.HTTPError:
            on_failure()
            return None

    results = await asyncio.gather(*(fetch(instance) for instance in instances))
    return dict(r for r in results if r is not None)
