"""Cold-Start-Platzierung (3.8): reine Entscheidungslogik, getrennt von der
API-Schicht testbar. Mit genau einem bekannten Knoten (`ClusterNode`, siehe
`sampler.py`) ist die "Wahl zwischen Knoten" trivial - die eigentliche
FFD-Bin-Packing-Logik ueber mehrere Knoten folgt erst in P10-S2. Diese Session
liefert bereits die vollstaendige Ressourcen-Schaetzung, Kapazitaetspruefung,
Singleton-Konflikterkennung und Audit-Persistierung."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

from plugin_orchestration_service.clients import RegistryClient
from plugin_orchestration_service.models import (
    NODE_ID_SELF,
    ClusterNode,
    PlacementDecision,
    PluginManifest,
    PluginResourceReport,
)
from plugin_orchestration_service.settings import Settings
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class PlacementError(Exception):
    """Basis fuer erwartete Ablehnungsgruende - die API-Schicht uebersetzt
    Subklassen in den passenden HTTP-Status."""


class ManifestNotFoundError(PlacementError):
    pass


class SingletonAlreadyPlacedError(PlacementError):
    pass


@dataclass
class ResourceEstimate:
    cpu_cores: float
    ram_mb: float
    source: str


async def _fresh_reports(
    session: AsyncSession, plugin_type: str, stale_after_seconds: float
) -> list[PluginResourceReport]:
    now = datetime.now(UTC)
    result = await session.execute(
        select(PluginResourceReport).where(PluginResourceReport.plugin_type == plugin_type)
    )
    return [
        report
        for report in result.scalars().all()
        if (now - report.reported_at).total_seconds() <= stale_after_seconds
    ]


def _estimate_resources(
    manifest: PluginManifest, fresh_reports: list[PluginResourceReport], settings: Settings
) -> ResourceEstimate:
    if manifest.resource_cpu_cores is not None and manifest.resource_ram_mb is not None:
        return ResourceEstimate(
            cpu_cores=manifest.resource_cpu_cores,
            ram_mb=manifest.resource_ram_mb,
            source="manifest",
        )
    if fresh_reports:
        return ResourceEstimate(
            cpu_cores=median(report.cpu_cores for report in fresh_reports),
            ram_mb=median(report.ram_mb for report in fresh_reports),
            source="observed_median",
        )
    return ResourceEstimate(
        cpu_cores=settings.default_cpu_cores,
        ram_mb=settings.default_ram_mb,
        source="default_fallback",
    )


def _check_capacity(
    node: ClusterNode | None, estimate: ResourceEstimate
) -> tuple[bool, str | None]:
    if node is None:
        return False, "Kein Knoten mit Ressourcen-Stichprobe verfuegbar"
    available_cpu_cores = node.cpu_cores * (1 - node.cpu_usage_percent / 100)
    if estimate.cpu_cores > available_cpu_cores or estimate.ram_mb > node.available_ram_mb:
        return False, "Nicht genug freie Kapazitaet auf dem einzigen bekannten Knoten"
    return True, None


async def decide_placement(
    session: AsyncSession,
    plugin_type: str,
    settings: Settings,
    registry_client: RegistryClient,
) -> PlacementDecision:
    manifest = await session.get(PluginManifest, plugin_type)
    if manifest is None:
        raise ManifestNotFoundError(plugin_type)

    fresh_reports = await _fresh_reports(
        session, plugin_type, settings.resource_report_stale_after_seconds
    )
    if manifest.scaling_type == "singleton" and fresh_reports:
        raise SingletonAlreadyPlacedError(plugin_type)

    estimate = _estimate_resources(manifest, fresh_reports, settings)
    node = await session.get(ClusterNode, NODE_ID_SELF)
    allowed, reason = _check_capacity(node, estimate)

    dependency_status = {
        dependency: await registry_client.has_healthy_instance(dependency)
        for dependency in manifest.dependencies
    }

    decision = PlacementDecision(
        plugin_type=plugin_type,
        node_id=node.node_id if allowed and node is not None else None,
        estimated_cpu_cores=estimate.cpu_cores,
        estimated_ram_mb=estimate.ram_mb,
        source=estimate.source,
        placement_allowed=allowed,
        reason=reason,
        dependency_status=dependency_status,
        decided_at=datetime.now(UTC),
    )
    session.add(decision)
    await session.flush()
    await session.refresh(decision)
    return decision
