"""`KubernetesSchedulerAdapter` (P24-S4, siehe ADR 0094) - mockt den
tatsaechlichen `kubernetes`-Client an der API-Aufrufgrenze
(`CoreV1Api.list_node`), nicht nur eine eigene Fake-Klasse, die zufaellig
dasselbe Interface erfuellt (das wuerde nichts ueber die reale
Implementierung aussagen). Es existiert in dieser Entwicklungsumgebung KEIN
echtes Kubernetes-Cluster (siehe docs/services/plugin-orchestration-service.md
"Grenzen dieser Ausbaustufe") - dieser gemockte Client ist die einzig
moegliche automatisierte Testform dafuer."""

from __future__ import annotations

from unittest.mock import MagicMock

from kubernetes import client
from plugin_orchestration_service.platform_scheduler import KubernetesSchedulerAdapter


def _node(
    name: str,
    *,
    cpu: str = "4",
    memory: str = "8Gi",
    ready: bool = True,
    unschedulable: bool = False,
) -> client.V1Node:
    return client.V1Node(
        metadata=client.V1ObjectMeta(name=name),
        spec=client.V1NodeSpec(unschedulable=unschedulable),
        status=client.V1NodeStatus(
            allocatable={"cpu": cpu, "memory": memory},
            conditions=[client.V1NodeCondition(type="Ready", status="True" if ready else "False")],
        ),
    )


def _mock_api(nodes: list[client.V1Node]) -> MagicMock:
    api = MagicMock(spec=client.CoreV1Api)
    api.list_node.return_value = client.V1NodeList(items=nodes)
    return api


async def test_node_with_enough_capacity_is_returned():
    api = _mock_api([_node("node-a", cpu="4", memory="8Gi")])
    adapter = KubernetesSchedulerAdapter(core_v1_api=api)

    node_id = await adapter.try_place(cpu_cores=1.0, ram_mb=512.0)

    assert node_id == "node-a"


async def test_no_node_with_enough_capacity_returns_none():
    api = _mock_api([_node("node-a", cpu="1", memory="1Gi")])
    adapter = KubernetesSchedulerAdapter(core_v1_api=api)

    node_id = await adapter.try_place(cpu_cores=4.0, ram_mb=8192.0)

    assert node_id is None


async def test_empty_cluster_returns_none():
    api = _mock_api([])
    adapter = KubernetesSchedulerAdapter(core_v1_api=api)

    node_id = await adapter.try_place(cpu_cores=0.1, ram_mb=64.0)

    assert node_id is None


async def test_multiple_fitting_nodes_picks_most_available_ram():
    """Tie-Break-Regel dieser ersten Version: unter mehreren passenden
    Knoten gewinnt der mit der meisten frei allokierbaren RAM-Kapazitaet
    ("most-available", spreizt Last) - siehe Klassendocstring in
    platform_scheduler.py fuer die Begruendung ggue. reinem First-Fit."""
    api = _mock_api(
        [
            _node("node-a", cpu="4", memory="8Gi"),
            _node("node-b", cpu="8", memory="16Gi"),
        ]
    )
    adapter = KubernetesSchedulerAdapter(core_v1_api=api)

    node_id = await adapter.try_place(cpu_cores=1.0, ram_mb=512.0)

    assert node_id == "node-b"


async def test_tie_break_on_equal_ram_uses_node_id_order():
    api = _mock_api(
        [
            _node("node-b", cpu="4", memory="8Gi"),
            _node("node-a", cpu="4", memory="8Gi"),
        ]
    )
    adapter = KubernetesSchedulerAdapter(core_v1_api=api)

    node_id = await adapter.try_place(cpu_cores=1.0, ram_mb=512.0)

    assert node_id == "node-a"


async def test_unschedulable_node_is_skipped():
    api = _mock_api(
        [
            _node("node-a", cpu="4", memory="8Gi", unschedulable=True),
            _node("node-b", cpu="4", memory="8Gi"),
        ]
    )
    adapter = KubernetesSchedulerAdapter(core_v1_api=api)

    node_id = await adapter.try_place(cpu_cores=1.0, ram_mb=512.0)

    assert node_id == "node-b"


async def test_not_ready_node_is_skipped():
    api = _mock_api(
        [
            _node("node-a", cpu="4", memory="8Gi", ready=False),
            _node("node-b", cpu="4", memory="8Gi"),
        ]
    )
    adapter = KubernetesSchedulerAdapter(core_v1_api=api)

    node_id = await adapter.try_place(cpu_cores=1.0, ram_mb=512.0)

    assert node_id == "node-b"


async def test_millicore_cpu_and_binary_memory_units_are_parsed():
    api = _mock_api([_node("node-a", cpu="500m", memory="536870912")])  # 512Mi in Bytes
    adapter = KubernetesSchedulerAdapter(core_v1_api=api)

    fits = await adapter.try_place(cpu_cores=0.4, ram_mb=500.0)
    does_not_fit = await adapter.try_place(cpu_cores=0.6, ram_mb=500.0)

    assert fits == "node-a"
    assert does_not_fit is None


async def test_label_selector_passed_through_to_list_node():
    api = _mock_api([_node("node-a")])
    adapter = KubernetesSchedulerAdapter(core_v1_api=api, node_label_selector="pool=plugins")

    await adapter.try_place(cpu_cores=0.1, ram_mb=64.0)

    api.list_node.assert_called_once_with(label_selector="pool=plugins")


async def test_no_label_selector_passes_none_to_list_node():
    api = _mock_api([_node("node-a")])
    adapter = KubernetesSchedulerAdapter(core_v1_api=api)

    await adapter.try_place(cpu_cores=0.1, ram_mb=64.0)

    api.list_node.assert_called_once_with(label_selector=None)
