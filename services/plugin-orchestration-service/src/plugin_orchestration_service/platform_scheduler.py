"""Platform scheduler preferred, FFD as fallback (3.8). P10-S0 decision
(clarifying question at session start): this repo has exclusively Docker
Compose as its real deploy target (no Swarm/Kubernetes) - the platform
scheduler branch was therefore initially built as a clean, concrete
interface, but only tested against a fake adapter, not against a real
cluster.

P24-S4 retrofits `KubernetesSchedulerAdapter` as a real adapter for exactly
this interface (see ADR 0094). `NullSchedulerAdapter` remains the actually
lived state in THIS Docker Compose development environment
(`KUBERNETES_SERVICE_HOST` is never set here) - `main.py` only chooses the
Kubernetes adapter when `detect_platform_scheduler()` actually returns a
hit, otherwise it still uses `NullSchedulerAdapter` -> FFD fallback."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from kubernetes.client import CoreV1Api

logger = logging.getLogger(__name__)


class SchedulerAdapter(Protocol):
    async def try_place(self, *, cpu_cores: float, ram_mb: float) -> str | None:
        """Returns a `node_id` if the platform itself has taken over
        placement, otherwise `None` (FFD fallback applies)."""
        ...


class NullSchedulerAdapter:
    """No connected platform scheduler - always FFD fallback."""

    async def try_place(self, *, cpu_cores: float, ram_mb: float) -> str | None:
        return None


class KubernetesSchedulerAdapter:
    """Real platform scheduler adapter for Kubernetes (P24-S4, see ADR 0094
    for the complete rationale behind the simplifications made here).

    `try_place` exclusively answers the placement question of the
    `SchedulerAdapter` interface ("which node HAS room") - the service
    therefore does NOT start any containers (see "Limitations of this
    stage" in `docs/services/plugin-orchestration-service.md`), that
    remains unchanged and outside its scope.

    Two deliberate simplifications for this first version:

    1. **In-cluster configuration only**
       (`kubernetes.config.load_incluster_config`). No kubeconfig path for
       out-of-cluster usage, because `detect_platform_scheduler()` only
       selects this adapter in the first place when
       `KUBERNETES_SERVICE_HOST` is set - i.e. the code is already running
       IN a pod. An additional kubeconfig path would be dead code weight
       for this call context.
    2. **Capacity check only against `status.allocatable` per node**, NOT
       against the sum of `resources.requests` of pods already running on
       that node. A node can therefore be falsely reported as suitable even
       though it is already fully utilized by other pods. Since
       `decide_placement()` trusts a return value from `try_place` WITHOUT
       its own re-check (see the docstring there), this is a real,
       deliberately accepted risk for this first version - not silently
       glossed over, see ADR 0094.

    Additionally, nodes are skipped that are cordoned per
    `spec.unschedulable` or whose `Ready` condition is not `"True"` - both
    readable directly from the node API without pod accounting, and a
    minimum standard that a real Kubernetes scheduler also observes.
    """

    def __init__(
        self,
        *,
        core_v1_api: CoreV1Api | None = None,
        node_label_selector: str = "",
    ) -> None:
        # `core_v1_api` is injectable (tests: mocked client, see
        # test_platform_scheduler_kubernetes.py). Production code
        # (`main.py`) passes nothing - the real client is built lazily on
        # the first `try_place` call via in-cluster config, not already at
        # object creation, so importing/constructing this class outside a
        # pod (e.g. during module import in tests) doesn't fail immediately.
        self._core_v1_api = core_v1_api
        self._node_label_selector = node_label_selector or None

    def _client(self) -> CoreV1Api:
        if self._core_v1_api is None:
            from kubernetes import client, config

            config.load_incluster_config()
            self._core_v1_api = client.CoreV1Api()
        return self._core_v1_api

    async def try_place(self, *, cpu_cores: float, ram_mb: float) -> str | None:
        api = self._client()
        # Blocking network call of the synchronous kubernetes client -
        # offloaded to a thread so it doesn't block the FastAPI event loop
        # (same principle as at every other synchronous I/O point in async
        # code).
        node_list = await asyncio.to_thread(api.list_node, label_selector=self._node_label_selector)

        candidates: list[tuple[str, float, float]] = []
        for node in node_list.items:
            capacity = _schedulable_node_capacity(node)
            if capacity is None:
                continue
            node_id, available_cpu_cores, available_ram_mb = capacity
            if cpu_cores <= available_cpu_cores and ram_mb <= available_ram_mb:
                candidates.append((node_id, available_cpu_cores, available_ram_mb))

        if not candidates:
            return None

        # Tie-break when multiple nodes match: the one with the most freely
        # allocatable RAM capacity wins ("most-available" instead of pure
        # first-fit) - spreads load rather than immediately filling up a
        # tightly matching node, which, given the simplification documented
        # above (no pod accounting), is the more cautious behavior. On a
        # RAM tie, `node_id` serves as a deterministic secondary tie-break.
        candidates.sort(key=lambda c: (-c[2], c[0]))
        best_node_id, _, _ = candidates[0]
        return best_node_id


def _schedulable_node_capacity(node: object) -> tuple[str, float, float] | None:
    """Returns `(node_id, available_cpu_cores, available_ram_mb)` for a node
    from `CoreV1Api.list_node()`, or `None` if the node is cordoned/not
    ready or lacks `node_id`/`allocatable` data. "Available" here means
    `status.allocatable` (see the class docstring for the deliberate
    simplification versus actually running pods)."""
    from kubernetes.utils import parse_quantity

    metadata = getattr(node, "metadata", None)
    node_id = getattr(metadata, "name", None) if metadata is not None else None
    if not node_id:
        return None

    spec = getattr(node, "spec", None)
    if spec is not None and getattr(spec, "unschedulable", False):
        return None

    node_status = getattr(node, "status", None)
    conditions = getattr(node_status, "conditions", None) or []
    ready = any(
        getattr(condition, "type", None) == "Ready" and getattr(condition, "status", None) == "True"
        for condition in conditions
    )
    if not ready:
        return None

    allocatable = getattr(node_status, "allocatable", None) or {}
    cpu_quantity = allocatable.get("cpu")
    memory_quantity = allocatable.get("memory")
    if cpu_quantity is None or memory_quantity is None:
        return None

    available_cpu_cores = float(parse_quantity(cpu_quantity))
    available_ram_mb = float(parse_quantity(memory_quantity)) / (1024 * 1024)
    return node_id, available_cpu_cores, available_ram_mb


def detect_platform_scheduler() -> str | None:
    """Purely informational detection (3.8) - returns the name of a
    detected platform or `None`. `KUBERNETES_SERVICE_HOST` is the usual
    signal for running in a Kubernetes pod. Since P24-S4, a real adapter
    exists for a positive hit (`KubernetesSchedulerAdapter`, see above) -
    `main.py` selects it based on this return value."""
    if os.environ.get("KUBERNETES_SERVICE_HOST"):
        return "kubernetes"
    return None
