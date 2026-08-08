from datetime import UTC, datetime, timedelta

import pytest
from plugin_orchestration_service import placement
from plugin_orchestration_service.models import (
    ClusterNode,
    PlacementDecision,
    PluginManifest,
    PluginResourceReport,
)
from plugin_orchestration_service.platform_scheduler import NullSchedulerAdapter
from plugin_orchestration_service.settings import Settings


class FakeRegistryClient:
    def __init__(self, healthy: dict[str, bool] | None = None) -> None:
        self._healthy = healthy or {}

    async def has_healthy_instance(self, service_type: str) -> bool:
        return self._healthy.get(service_type, False)


class FakeSchedulerAdapter:
    def __init__(self, node_id: str | None = None) -> None:
        self._node_id = node_id

    async def try_place(self, *, cpu_cores: float, ram_mb: float) -> str | None:
        return self._node_id


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


async def _decide(
    session,
    plugin_type,
    *,
    settings: Settings | None = None,
    registry_client=None,
    scheduler_adapter=None,
) -> PlacementDecision:
    return await placement.decide_placement(
        session,
        plugin_type,
        settings or _settings(),
        registry_client or FakeRegistryClient(),
        scheduler_adapter or NullSchedulerAdapter(),
    )


async def _add_manifest(session, **overrides) -> PluginManifest:
    now = datetime.now(UTC)
    defaults = dict(
        plugin_type="cmis-connector",
        version="1.0.0",
        scaling_type="stateless_horizontal",
        resource_cpu_cores=None,
        resource_ram_mb=None,
        load_profile=None,
        dependencies=[],
        registered_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    manifest = PluginManifest(**defaults)
    session.add(manifest)
    await session.commit()
    return manifest


async def _add_node(
    session,
    *,
    node_id="self",
    cpu_cores=4.0,
    total_ram_mb=8192.0,
    cpu_usage_percent=0.0,
    available_ram_mb=8192.0,
):
    node = ClusterNode(
        node_id=node_id,
        cpu_cores=cpu_cores,
        total_ram_mb=total_ram_mb,
        cpu_usage_percent=cpu_usage_percent,
        available_ram_mb=available_ram_mb,
        sampled_at=datetime.now(UTC),
    )
    session.add(node)
    await session.commit()
    return node


async def _add_report(session, *, instance_id, plugin_type, cpu_cores, ram_mb, age_seconds=0):
    report = PluginResourceReport(
        instance_id=instance_id,
        plugin_type=plugin_type,
        cpu_cores=cpu_cores,
        ram_mb=ram_mb,
        reported_at=datetime.now(UTC) - timedelta(seconds=age_seconds),
    )
    session.add(report)
    await session.commit()
    return report


async def _add_decision(session, *, plugin_type, node_id, allowed=True):
    decision = PlacementDecision(
        plugin_type=plugin_type,
        node_id=node_id,
        estimated_cpu_cores=0.1,
        estimated_ram_mb=64.0,
        source="manifest",
        placement_method="ffd",
        placement_allowed=allowed,
        reason=None,
        dependency_status={},
        decided_at=datetime.now(UTC),
    )
    session.add(decision)
    await session.commit()
    return decision


async def test_manifest_not_found_raises(session):
    with pytest.raises(placement.ManifestNotFoundError):
        await _decide(session, "unknown")


async def test_uses_manifest_resource_profile_when_declared(session):
    await _add_manifest(session, resource_cpu_cores=1.0, resource_ram_mb=512.0)
    await _add_node(session)

    decision = await _decide(session, "cmis-connector")

    assert decision.source == "manifest"
    assert decision.estimated_cpu_cores == 1.0
    assert decision.estimated_ram_mb == 512.0
    assert decision.placement_allowed is True
    assert decision.node_id == "self"
    assert decision.placement_method == "ffd"


async def test_falls_back_to_observed_median_without_manifest_values(session):
    await _add_manifest(session)
    await _add_node(session)
    await _add_report(
        session, instance_id="a", plugin_type="cmis-connector", cpu_cores=1.0, ram_mb=100.0
    )
    await _add_report(
        session, instance_id="b", plugin_type="cmis-connector", cpu_cores=3.0, ram_mb=300.0
    )

    decision = await _decide(session, "cmis-connector")

    assert decision.source == "observed_median"
    assert decision.estimated_cpu_cores == 2.0
    assert decision.estimated_ram_mb == 200.0


async def test_falls_back_to_default_without_manifest_values_or_reports(session):
    await _add_manifest(session)
    await _add_node(session)

    decision = await _decide(
        session, "cmis-connector", settings=_settings(default_cpu_cores=0.25, default_ram_mb=128.0)
    )

    assert decision.source == "default_fallback"
    assert decision.estimated_cpu_cores == 0.25
    assert decision.estimated_ram_mb == 128.0


async def test_stale_reports_excluded_from_median(session):
    await _add_manifest(session)
    await _add_node(session)
    await _add_report(
        session, instance_id="fresh", plugin_type="cmis-connector", cpu_cores=1.0, ram_mb=100.0
    )
    await _add_report(
        session,
        instance_id="stale",
        plugin_type="cmis-connector",
        cpu_cores=99.0,
        ram_mb=9999.0,
        age_seconds=120,
    )

    decision = await _decide(
        session, "cmis-connector", settings=_settings(resource_report_stale_after_seconds=60.0)
    )

    assert decision.source == "observed_median"
    assert decision.estimated_cpu_cores == 1.0
    assert decision.estimated_ram_mb == 100.0


async def test_singleton_already_placed_rejected(session):
    await _add_manifest(session, scaling_type="singleton")
    await _add_node(session)
    await _add_report(
        session, instance_id="existing", plugin_type="cmis-connector", cpu_cores=0.5, ram_mb=128.0
    )

    with pytest.raises(placement.SingletonAlreadyPlacedError):
        await _decide(session, "cmis-connector")


async def test_singleton_allowed_when_no_existing_instance(session):
    await _add_manifest(
        session, scaling_type="singleton", resource_cpu_cores=0.5, resource_ram_mb=128.0
    )
    await _add_node(session)

    decision = await _decide(session, "cmis-connector")

    assert decision.placement_allowed is True


async def test_insufficient_capacity_is_rejected_but_still_recorded(session):
    await _add_manifest(session, resource_cpu_cores=8.0, resource_ram_mb=100.0)
    await _add_node(session, cpu_cores=4.0, cpu_usage_percent=0.0)

    decision = await _decide(session, "cmis-connector")

    assert decision.placement_allowed is False
    assert decision.node_id is None
    assert decision.reason is not None


async def test_no_node_sampled_yet_is_rejected(session):
    await _add_manifest(session, resource_cpu_cores=0.5, resource_ram_mb=128.0)

    decision = await _decide(session, "cmis-connector")

    assert decision.placement_allowed is False
    assert decision.node_id is None


async def test_dependency_status_reflects_registry_client(session):
    await _add_manifest(
        session,
        resource_cpu_cores=0.5,
        resource_ram_mb=128.0,
        dependencies=["storage-service", "search-service"],
    )
    await _add_node(session)

    decision = await _decide(
        session,
        "cmis-connector",
        registry_client=FakeRegistryClient({"storage-service": True, "search-service": False}),
    )

    assert decision.dependency_status == {"storage-service": True, "search-service": False}


async def test_ffd_skips_undersized_node_and_picks_next_that_fits(session):
    await _add_manifest(session, resource_cpu_cores=2.0, resource_ram_mb=512.0)
    await _add_node(
        session, node_id="node-a", cpu_cores=1.0, total_ram_mb=1024.0, available_ram_mb=1024.0
    )
    await _add_node(
        session, node_id="node-b", cpu_cores=4.0, total_ram_mb=4096.0, available_ram_mb=4096.0
    )

    decision = await _decide(session, "cmis-connector")

    assert decision.placement_allowed is True
    assert decision.node_id == "node-b"


async def test_ffd_picks_first_node_in_id_order_when_multiple_fit(session):
    await _add_manifest(session, resource_cpu_cores=0.5, resource_ram_mb=128.0)
    await _add_node(
        session, node_id="node-a", cpu_cores=4.0, total_ram_mb=4096.0, available_ram_mb=4096.0
    )
    await _add_node(
        session, node_id="node-b", cpu_cores=4.0, total_ram_mb=4096.0, available_ram_mb=4096.0
    )

    decision = await _decide(session, "cmis-connector")

    assert decision.node_id == "node-a"


async def test_time_profile_prefers_node_with_complementary_occupant(session):
    await _add_node(
        session, node_id="node-a", cpu_cores=4.0, total_ram_mb=4096.0, available_ram_mb=4096.0
    )
    await _add_node(
        session, node_id="node-b", cpu_cores=4.0, total_ram_mb=4096.0, available_ram_mb=4096.0
    )

    # node-a hosts a complementary ("interactive") occupant, node-b hosts a
    # competing ("nightly") occupant - both alive (fresh report + allowed
    # decision pointing at their respective node).
    await _add_manifest(session, plugin_type="interactive-plugin", load_profile="interactive")
    await _add_report(
        session,
        instance_id="interactive-1",
        plugin_type="interactive-plugin",
        cpu_cores=0.1,
        ram_mb=64.0,
    )
    await _add_decision(session, plugin_type="interactive-plugin", node_id="node-a")

    await _add_manifest(session, plugin_type="nightly-plugin", load_profile="nightly")
    await _add_report(
        session, instance_id="nightly-1", plugin_type="nightly-plugin", cpu_cores=0.1, ram_mb=64.0
    )
    await _add_decision(session, plugin_type="nightly-plugin", node_id="node-b")

    await _add_manifest(
        session,
        plugin_type="new-nightly-plugin",
        load_profile="nightly",
        resource_cpu_cores=0.5,
        resource_ram_mb=128.0,
    )

    decision = await _decide(session, "new-nightly-plugin")

    assert decision.node_id == "node-a"


async def test_no_load_profile_ignores_time_profile_ranking(session):
    await _add_node(
        session, node_id="node-a", cpu_cores=4.0, total_ram_mb=4096.0, available_ram_mb=4096.0
    )
    await _add_node(
        session, node_id="node-b", cpu_cores=4.0, total_ram_mb=4096.0, available_ram_mb=4096.0
    )
    await _add_manifest(session, resource_cpu_cores=0.5, resource_ram_mb=128.0, load_profile=None)

    decision = await _decide(session, "cmis-connector")

    # Ohne load_profile bleibt reines First-Fit (node-a zuerst in id-Reihenfolge).
    assert decision.node_id == "node-a"


async def test_scheduler_adapter_delegation_skips_ffd_entirely(session):
    await _add_manifest(session, resource_cpu_cores=0.5, resource_ram_mb=128.0)
    # Bewusst KEIN ClusterNode angelegt - beweist, dass der FFD-Pfad gar
    # nicht erst konsultiert wird, wenn der Plattform-Scheduler liefert.

    decision = await _decide(
        session, "cmis-connector", scheduler_adapter=FakeSchedulerAdapter("external-node-1")
    )

    assert decision.placement_method == "platform_scheduler"
    assert decision.node_id == "external-node-1"
    assert decision.placement_allowed is True


async def test_scheduler_adapter_unavailable_falls_back_to_ffd(session):
    await _add_manifest(session, resource_cpu_cores=0.5, resource_ram_mb=128.0)
    await _add_node(session)

    decision = await _decide(
        session, "cmis-connector", scheduler_adapter=FakeSchedulerAdapter(None)
    )

    assert decision.placement_method == "ffd"
    assert decision.node_id == "self"
