"""Platzierung (3.8): Plattform-Scheduler bevorzugt (siehe
`platform_scheduler.py`), First-Fit-Decreasing ueber alle bekannten Knoten
als Fallback, Zeitprofil-bewusste Knotenauswahl vor der reinen
Kapazitaetspruefung (P10-S2). Reine Entscheidungslogik, getrennt von der
API-Schicht testbar."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from plugin_orchestration_service.clients import RegistryClient
from plugin_orchestration_service.models import (
    ClusterNode,
    PlacementDecision,
    PluginManifest,
    PluginResourceReport,
)
from plugin_orchestration_service.platform_scheduler import SchedulerAdapter
from plugin_orchestration_service.settings import Settings


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


async def _all_nodes(session: AsyncSession) -> list[ClusterNode]:
    """Aufsteigend nach `node_id` fuer eine deterministische First-Fit-
    Reihenfolge (Konzept 3.8 sortiert nur die zu platzierenden Instanzen,
    nicht die Knoten - eine feste, nachvollziehbare Knotenreihenfolge ist
    hier die einfachste korrekte Wahl)."""
    result = await session.execute(select(ClusterNode).order_by(ClusterNode.node_id))
    return list(result.scalars().all())


async def _current_occupants(
    session: AsyncSession, stale_after_seconds: float
) -> dict[str, list[str | None]]:
    """`node_id` -> Liste der `load_profile`-Werte der dort aktuell
    "lebenden" Plugin-Typen. "Lebend" = ein `plugin_type` mit einem frischen
    `PluginResourceReport` UND dessen juengste erlaubte `PlacementDecision`
    zeigt auf diesen Knoten. Grundlage der Zeitprofil-bewussten
    Knotenauswahl (siehe `_rank_nodes`)."""
    now = datetime.now(UTC)
    reports_result = await session.execute(select(PluginResourceReport))
    alive_types = {
        report.plugin_type
        for report in reports_result.scalars().all()
        if (now - report.reported_at).total_seconds() <= stale_after_seconds
    }
    occupants: dict[str, list[str | None]] = {}
    for plugin_type in alive_types:
        decision_result = await session.execute(
            select(PlacementDecision)
            .where(
                PlacementDecision.plugin_type == plugin_type,
                PlacementDecision.placement_allowed.is_(True),
            )
            .order_by(PlacementDecision.decided_at.desc())
            .limit(1)
        )
        decision = decision_result.scalars().first()
        if decision is None or decision.node_id is None:
            continue
        manifest = await session.get(PluginManifest, plugin_type)
        load_profile = manifest.load_profile if manifest is not None else None
        occupants.setdefault(decision.node_id, []).append(load_profile)
    return occupants


def _profile_score(node_id: str, occupants: dict[str, list[str | None]], load_profile: str) -> int:
    profiles = occupants.get(node_id, [])
    complementary = sum(1 for p in profiles if p is not None and p != load_profile)
    competing = sum(1 for p in profiles if p == load_profile)
    return complementary - competing


def _rank_nodes(
    nodes: list[ClusterNode], occupants: dict[str, list[str | None]], load_profile: str | None
) -> list[ClusterNode]:
    """Ohne `load_profile` (heute der Regelfall - kein echtes Plugin
    deklariert eins) bleibt die First-Fit-Reihenfolge unveraendert. Mit
    `load_profile` werden Knoten nach Komplementaritaets-Score sortiert -
    `sorted()` ist stabil, gleiche Scores behalten die urspruengliche
    `node_id`-Reihenfolge (First Fit als Tie-Break)."""
    if load_profile is None:
        return nodes
    return sorted(nodes, key=lambda node: -_profile_score(node.node_id, occupants, load_profile))


def _select_node(
    ranked_nodes: list[ClusterNode], estimate: ResourceEstimate
) -> tuple[ClusterNode | None, str | None]:
    for node in ranked_nodes:
        available_cpu_cores = node.cpu_cores * (1 - node.cpu_usage_percent / 100)
        if estimate.cpu_cores <= available_cpu_cores and estimate.ram_mb <= node.available_ram_mb:
            return node, None
    if not ranked_nodes:
        return None, "Kein Knoten mit Ressourcen-Stichprobe verfuegbar"
    return None, "Nicht genug freie Kapazitaet auf allen bekannten Knoten"


async def decide_placement(
    session: AsyncSession,
    plugin_type: str,
    settings: Settings,
    registry_client: RegistryClient,
    scheduler_adapter: SchedulerAdapter,
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

    # Plattform-Scheduler bevorzugt (3.8): liefert er einen Knoten, wird ihm
    # vollstaendig vertraut - keine eigene Kapazitaetspruefung mehr, siehe
    # Konzept ("vertraut der Plugin Orchestration Service dessen
    # Platzierungsentscheidungen").
    scheduler_node_id = await scheduler_adapter.try_place(
        cpu_cores=estimate.cpu_cores, ram_mb=estimate.ram_mb
    )
    if scheduler_node_id is not None:
        node_id: str | None = scheduler_node_id
        allowed = True
        reason: str | None = None
        placement_method = "platform_scheduler"
    else:
        nodes = await _all_nodes(session)
        occupants = await _current_occupants(session, settings.resource_report_stale_after_seconds)
        ranked_nodes = _rank_nodes(nodes, occupants, manifest.load_profile)
        node, reason = _select_node(ranked_nodes, estimate)
        node_id = node.node_id if node is not None else None
        allowed = node is not None
        placement_method = "ffd"

    dependency_status = {
        dependency: await registry_client.has_healthy_instance(dependency)
        for dependency in manifest.dependencies
    }

    decision = PlacementDecision(
        plugin_type=plugin_type,
        node_id=node_id,
        estimated_cpu_cores=estimate.cpu_cores,
        estimated_ram_mb=estimate.ram_mb,
        source=estimate.source,
        placement_method=placement_method,
        placement_allowed=allowed,
        reason=reason,
        dependency_status=dependency_status,
        decided_at=datetime.now(UTC),
    )
    session.add(decision)
    await session.flush()
    await session.refresh(decision)
    return decision
