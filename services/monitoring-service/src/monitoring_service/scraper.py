from __future__ import annotations

import asyncio
from collections.abc import Callable

import httpx
from monitoring_service.clients import RegistryInstance
from prometheus_client.metrics_core import Metric
from prometheus_client.parser import text_string_to_metric_families
from prometheus_client.samples import Sample


def merge_metric_families(bodies: dict[str, str]) -> list[Metric]:
    """Führt die roh gescrapten Exposition-Texte mehrerer Instanzen zu einer
    Liste von Metrik-Familien zusammen - echtes Proxy/Federation-Muster statt
    Text-Konkatenation: bloßes Aneinanderhängen würde bei gleichnamigen
    Metriken über mehrere Instanzen hinweg die Gruppierungsregel des
    Exposition-Formats verletzen (HELP/TYPE müssen zusammenhängend stehen).
    Jede Sample bekommt stattdessen ein zusätzliches `instance`-Label zur
    Disambiguierung."""
    merged: dict[str, Metric] = {}
    for instance_id, body in bodies.items():
        for family in text_string_to_metric_families(body):
            target = merged.get(family.name)
            if target is None:
                target = Metric(family.name, family.documentation, family.type)
                merged[family.name] = target
            for sample in family.samples:
                labels = {**sample.labels, "instance": instance_id}
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
    """Ruft `/metrics` aller übergebenen Instanzen parallel ab. Ein
    fehlschlagendes Ziel blockiert die anderen nicht (`asyncio.gather` ohne
    `return_exceptions`-Propagation nach außen) und erhöht `on_failure()`
    (der immer-aktive `monitoring.scrape.failures_total`-Zähler, siehe
    `main.py` - bewusst außerhalb des konfigurierbaren Sensor-Katalogs)."""

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
