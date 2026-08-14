# 0094 — plugin-orchestration-service: real `KubernetesSchedulerAdapter`

**Status:** accepted (P24-S4, see `IMPLEMENTATION_PLAN.md`)
**Context:** Concept 3.8, affects `plugin-orchestration-service`

## Decision

`platform_scheduler.py` gains a real implementation of the existing `SchedulerAdapter` interface via
`KubernetesSchedulerAdapter` (previously only `NullSchedulerAdapter`, P10-S0/P10-S2). Important: the
interface has exactly one method, `try_place(*, cpu_cores, ram_mb) -> str | None` — a pure **placement
recommendation** ("which node has room"), not a container lifecycle API. The service still does not
start/stop containers (see "Limits of This Build-Out Stage" in
`docs/services/plugin-orchestration-service.md`) — that remains unchanged out of scope, this session
doesn't change that.

`main.py` selects `KubernetesSchedulerAdapter` only when `detect_platform_scheduler()` actually returns
`"kubernetes"` (`KUBERNETES_SERVICE_HOST` set, i.e. a real pod context) — in this Docker Compose
development environment, `NullSchedulerAdapter` remains unchanged as the real, lived state.

Two deliberate simplifications for this first version:

1. **In-cluster configuration only** (`kubernetes.config.load_incluster_config()`), no kubeconfig path
   for out-of-cluster use.
2. **Capacity check solely against `status.allocatable` per node**, not against the sum of
   `resources.requests` of pods already running on that node (no pod-usage-aware bin packing).
   Additionally, non-schedulable nodes (`spec.unschedulable`) and non-`Ready` nodes are skipped — this is
   possible using pure node API information without pod accounting and is a minimum standard that a real
   scheduler also observes.

Tie-break when multiple nodes qualify: the one with the most free allocatable RAM capacity wins
("most-available", spreads load), with `node_id` as a deterministic secondary tie-break on a further tie.

## Rationale

- **Why in-cluster config only, without additional kubeconfig support**: `detect_platform_scheduler()`
  selects this adapter only when `KUBERNETES_SERVICE_HOST` is set — this signal only exists when the code
  is already itself running inside a pod (an official Kubernetes mechanism for in-pod service discovery).
  A calling context where the adapter becomes active WITHOUT the process running in a pod doesn't exist
  in the current design — an additional kubeconfig loading path would be dead code with no intended
  caller, plus a second, untested authentication path. Should a future session identify a need for
  out-of-cluster operation (e.g. an admin tool talking to a foreign cluster), that's a deliberate new
  decision, not a silently omitted feature of this session.
- **Why no pod-usage-aware capacity check (no real bin packing)**: that would have additionally required
  `list_pod_for_all_namespaces()` (or per-node `list_namespaced_pod` across all namespaces), summing every
  pod request and correctly accounting for its phase (`Running`/`Pending` vs. `Succeeded`/`Failed`) —
  noticeably more complexity, additional RBAC permissions (pod read access across all namespaces, not
  just node read access) and, without a real cluster here, not meaningfully verifiable against real data
  (see "Consequences"). `status.allocatable` alone is an explicitly documented, honest simplification for
  a first version, not a concealed trade-off — the consequence is named below.
- **Why this is acceptable despite the "no downstream re-check" pitfall**: `decide_placement()` fully
  trusts a return value from `try_place`, without its own capacity check (see the docstring there). A
  purely allocatable-based adapter can therefore recommend a node that is actually fully utilized. This is
  deliberately accepted here as a documented risk of a first version (analogous to this project's general
  principle of honestly naming unfinished features rather than faking them) instead of being "solved" via
  premature complexity that wouldn't be reliably verifiable without a real cluster anyway.
- **Why ready/unschedulable filtering was still built in, even though that already goes beyond a pure
  simplification**: both signals are available directly, with no additional API calls, in the already-
  loaded `list_node()` response — ignoring them would be no simplification gain, just an unnecessarily
  crude implementation that would obviously recommend wrong nodes (a drained/broken node).
- **Why "most-available RAM" instead of pure first-fit as the tie-break**: the existing FFD fallback path
  in `placement.py` already uses first-fit (fixed `node_id` order, see the docstring there) — for the
  Kubernetes branch a different rule is deliberately chosen, because here (unlike the FFD fallback) no pod
  accounting takes place: preferring the least-utilized node reduces the risk of recommending a node that
  is actually already tight somewhat more than pure first-fit would — no substitute for real pod
  accounting, but a sensible shift of the residual risk.

## Consequences

- **No real Kubernetes cluster in this development environment** (the only real deploy target remains
  Docker Compose, Phase 26 only brings `helm lint`/`helm template`-verified charts without a real cluster
  deployment) — `KubernetesSchedulerAdapter` is therefore tested exclusively against a mocked
  `kubernetes` client (`tests/test_platform_scheduler_kubernetes.py`), never against a real API server.
  This is an honestly named testing gap, not a concealed one — see
  `docs/services/plugin-orchestration-service.md`.
- **New hard dependency**: the official `kubernetes` PyPI package (Python client) in
  `services/plugin-orchestration-service/pyproject.toml`.
- **New setting** `kubernetes_node_label_selector` (default empty = all nodes) for optionally scoping
  node selection to part of the cluster (e.g. a dedicated plugin node pool).
- Should a future session (at the earliest with real cluster access, outside today's roadmap) want to add
  pod-usage-aware bin packing, that's a separate, deliberate step — not automatically part of this
  session.
