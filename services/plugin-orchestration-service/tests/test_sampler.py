from plugin_orchestration_service import sampler
from plugin_orchestration_service.models import NODE_ID_SELF, ClusterNode


def test_sample_local_node_returns_plausible_values():
    values = sampler.sample_local_node()

    assert values["cpu_cores"] >= 1
    assert values["total_ram_mb"] > 0
    assert 0 <= values["cpu_usage_percent"] <= 100
    assert 0 <= values["available_ram_mb"] <= values["total_ram_mb"]


async def test_run_tick_creates_self_node(session):
    await sampler.run_tick(session)

    node = await session.get(ClusterNode, NODE_ID_SELF)
    assert node is not None
    assert node.cpu_cores >= 1


async def test_run_tick_updates_existing_self_node(session):
    await sampler.run_tick(session)
    first = await session.get(ClusterNode, NODE_ID_SELF)
    first_sampled_at = first.sampled_at

    await sampler.run_tick(session)
    second = await session.get(ClusterNode, NODE_ID_SELF)

    assert second.sampled_at >= first_sampled_at
