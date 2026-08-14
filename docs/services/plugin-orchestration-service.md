# plugin-orchestration-service

**Responsibility:** Plugin Orchestration Service (3.8) — manages manifests of "pluggable" elements (connectors, rendering backends, rule plugins, ...), makes and audits placement decisions (platform scheduler preferred, first-fit-decreasing across multiple nodes as a fallback, load-profile-aware node selection), samples its own, deliberately minimal resource snapshot. Answers the question "where should something run" — as opposed to `registry-service`, which only answers "who is currently reachable and licensed" (Concept 3.8 "Distinction from the Registry").

**Concept reference:** 3.8
**Own Postgres schema:** `orchestration` (tables `plugin_manifest`, `plugin_resource_report`, `cluster_node`, `placement_decision`).

## Boundaries of This Build-Out Stage (P10-S1/S2)

Deliberate scope decisions from follow-up questions at session start, see `PROGRESS.md` "Orchestration & Rolling Updates":

- **A decision/recommendation engine, not a container lifecycle manager.** The service computes and audits placement decisions but does not itself start containers — no Docker socket access, no new security risk. Actual starting/stopping remains external (human/deploy script), analogous to the P8-S3 precedent (features without a real backend are documented rather than faked). For the same reason, the orchestrator does **not** trigger an automatic drain on `registry-service` (see `docs/services/registry-service.md` "Drain Mechanism") — only the state/its enforcement was built in P10-S2, not an automatic coupling.
- **Exactly one really sampled node** (`cluster_node`, `node_id="self"`, via `psutil`) — in the real Docker Compose environment there is only this one host anyway. Since P10-S2, further nodes can be declared via `POST /nodes/{node_id}` (no real second host calls this today, the same "no real caller, but the same self-reporting logic" pattern as the resource self-reporting).
- **Platform scheduler branch: `NullSchedulerAdapter` remains this project's real state, but since P24-S4 a real `KubernetesSchedulerAdapter` exists** (mock-tested only, see below and [ADR 0094](../adr/0094-plugin-orchestration-kubernetes-scheduler-adapter.md)) — `main.py` only wires it up if `detect_platform_scheduler()` actually returns `"kubernetes"` (`KUBERNETES_SERVICE_HOST` set). In this Docker Compose development environment, this variable is never set, so `NullSchedulerAdapter` → FFD fallback remains the lived state unchanged.
- **No Docker API for resource measurement** — plugin instances actively report their own, self-measured resource usage (e.g. via `psutil.Process()`) via `POST /plugins/{plugin_type}/resource-usage`, analogous to the Registry heartbeat principle. No real plugin calls this today (none exists yet) — covered synthetically by tests, the same pattern as `registry-service` before P9-S2.
- **Implicit profile derivation from historical observation is not built** (Concept 3.8 explicitly names it as optional) — only `load_profile` values explicitly declared in the manifest feed into node selection.
- **10.1 sensor infrastructure only exists from Phase 11 onward** (P10-S0 finding) — the service's own snapshot here is a deliberately minimal transitional solution, not an anticipation of the full-fledged monitoring layer.

## Architectural Decisions

- **Manifest format 1:1 from the concept text**: `plugin_type` (primary key, upsert instead of version coexistence), `version`, `scaling_type` (`"stateless_horizontal"`/`"singleton"`), `resource_cpu_cores`/`resource_ram_mb` (optional), `load_profile` (optional, free-form string), `dependencies` (list of other `service_type` values).
- **Cold-start resource estimation with three sources** (`source` field in `PlacementDecisionOut`): `"manifest"` (static values declared in the manifest), `"observed_median"` (median of fresh `PluginResourceReport` values of the same `plugin_type`, Concept 3.8 verbatim), `"default_fallback"` (documented minimal default `0.5` cores/`256` MB, if neither manifest nor observation is available — a distinct value to distinguish this "cold" special case from the real median case). Reports older than `resource_report_stale_after_seconds` (default 60s) do not count.
- **Singleton conflict detection**: `scaling_type="singleton"` + a fresh resource report from another instance of the same type → `POST /placements` returns `409` instead of a second placement.
- **First-fit-decreasing across all known nodes** (P10-S2): nodes are iterated in ascending `node_id` order (Concept 3.8 only sorts the instances to be placed, not the nodes — a fixed order is the simplest correct choice), and the first one with sufficient free capacity (available cores = `cpu_cores * (1 - cpu_usage_percent/100)`) is chosen. If no capacity suffices, the decision is still persisted (`placement_allowed=false`, `node_id=null`, `reason` set) rather than discarded — the audit obligation from 3.8 also applies to rejected requests.
- **Load-profile-aware node selection before the plain first-fit** (P10-S2, Concept 3.8: load profile "as an additional sort/grouping criterion **before** the pure resource-size sort"): if the plugin to be placed has a `load_profile`, capacity-capable nodes are sorted by the complementarity of the plugin types currently "living" there — "living" = a `plugin_type` with a fresh `PluginResourceReport` **and** whose most recent allowed `PlacementDecision.node_id` points to this node. Score = number of types living there with a **different** `load_profile` minus number with the **same** `load_profile` (example: a night job prefers a node that is busy during the day with interactive load). Without a `load_profile` (the standard case today), it remains pure first-fit.
- **Platform scheduler preferred** (P10-S2, Concept 3.8): `decide_placement` first asks the injected `SchedulerAdapter`; if it returns a node, it is fully trusted (no own capacity check, `placement_method="platform_scheduler"`), otherwise first-fit+load-profile kicks in (`placement_method="ffd"`). In this Docker Compose development environment, it is always `NullSchedulerAdapter` → always `"ffd"` (see "Boundaries of This Build-Out Stage").
- **`KubernetesSchedulerAdapter`** (P24-S4, [ADR 0094](../adr/0094-plugin-orchestration-kubernetes-scheduler-adapter.md)) implements `SchedulerAdapter` for real against the Kubernetes API. Activation: `main.py` selects it exactly when `detect_platform_scheduler()` returns `"kubernetes"` (the `KUBERNETES_SERVICE_HOST` env var set — this signal only exists if the process itself is running in a pod), otherwise it stays with `NullSchedulerAdapter`. Authentication exclusively via `kubernetes.config.load_incluster_config()` — deliberately **no** kubeconfig path for out-of-cluster use (rationale in ADR 0094). `try_place`:
  1. Lists cluster nodes via `CoreV1Api.list_node()` (optionally scoped to `settings.kubernetes_node_label_selector`, default all nodes), the blocking client call offloaded via `asyncio.to_thread`.
  2. Skips nodes with `spec.unschedulable=true` or without a `Ready` condition.
  3. Compares `cpu_cores`/`ram_mb` against `status.allocatable` for each remaining node (parsing Kubernetes quantity strings like `"500m"`/`"8Gi"` via `kubernetes.utils.parse_quantity`).
  4. **Capacity simplification (a documented limitation, not a concealed compromise)**: only `status.allocatable` is checked, NOT the sum of `resources.requests` of pods already running on the node — a node can therefore be incorrectly reported as fitting even though it is already saturated by other pods. Since `decide_placement()` trusts a return value from `try_place` without its own follow-up check, this is a real, deliberately accepted risk for this first version (see ADR 0094 for the trade-off).
  5. Tie-break with multiple fitting nodes: the one with the most freely allocatable RAM capacity wins ("most-available", spreads load more than plain first-fit), with a tie, `node_id` serves as a deterministic secondary tie-break.
  **Test gap, honestly stated (this project's honesty convention)**: no real Kubernetes cluster exists in this environment (the only real deploy target remains Docker Compose; Phase 26 only brings `helm lint`/`helm template`-verified charts without a real cluster deployment) — `tests/test_platform_scheduler_kubernetes.py` therefore necessarily mocks the `kubernetes` client itself (`CoreV1Api.list_node`, real `kubernetes.client` model objects such as `V1Node`/`V1NodeStatus` as return values) instead of running against a real API server. A run against a real cluster has not taken place and was not possible in this environment.
- **Every placement decision is audited** (3.8: "the Plugin Orchestration Service itself is also a service whose decisions are audited") — locally in `placement_decision` (a read model for `GET /placements`) AND as an `orchestration.placement.decided` event, which `audit-service` consumes (`"orchestration.>"`).
- **Dependency check is informative, not blocking** — the `dependencies` listed in the manifest are checked against `registry-service`'s `GET /instances/{type}` (TTL cache, fail-open) and returned as `dependency_status`, but do not prevent placement (the concept does not require it).
- **New domain-separated admin role `domain-admin-orchestration` (`admin.orchestration`)** — created, like `admin.license` (P9-S1), only once the actual feature exists; Concept 4.6 names its domain list only as an example. Gates `POST /plugins/{type}` and `POST /placements`; `GET` endpoints and resource-usage self-reporting remain ungated (service-to-service, no principal).
- **Atomic upsert (`ON CONFLICT DO UPDATE`) instead of get-then-create for `cluster_node`** — a deliberate deviation from the otherwise usual pattern (e.g. `registry_service.repository.register`), because here two concurrent writers can actually hit the same row (the background sampler loop + e.g. a test that seeds the node) — a get-then-insert would be a real race condition, not merely a theoretical risk (actually occurred as a `UniqueViolationError` during test development).

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/plugins/{plugin_type}` | Register/update a manifest (upsert). Requires `admin.orchestration` or an activated superuser. |
| `GET` | `/plugins` | All manifests. Ungated. |
| `GET` | `/plugins/{plugin_type}` | A single manifest, `404` if unknown. Ungated. |
| `POST` | `/plugins/{plugin_type}/resource-usage` | Resource self-report of a running instance (`instance_id`/`cpu_cores`/`ram_mb`). Ungated, service-to-service. |
| `GET` | `/nodes` | Sampled/declared nodes. Ungated. |
| `POST` | `/nodes/{node_id}` | Capacity self-report of a (further) node (upsert, P10-S2). Requires `admin.orchestration` or an activated superuser. |
| `POST` | `/placements` | Request a placement decision. `404` on unknown manifest, `409` on singleton conflict. Requires `admin.orchestration` or an activated superuser. |
| `GET` | `/placements` | Placement history (audit read model), optionally `?plugin_type=`. Ungated. |

## Data Model

- `plugin_manifest` — `plugin_type` (PK), `version`, `scaling_type`, `resource_cpu_cores`/`resource_ram_mb` (nullable), `load_profile` (nullable), `dependencies` (JSON list), `registered_at`/`updated_at`.
- `plugin_resource_report` — `instance_id` (PK), `plugin_type`, `cpu_cores`, `ram_mb`, `reported_at`.
- `cluster_node` — `node_id` (PK, `"self"` = own sampled host, further ones via `POST /nodes/{id}`), `cpu_cores`, `total_ram_mb`, `cpu_usage_percent`, `available_ram_mb`, `sampled_at`.
- `placement_decision` — `id`, `plugin_type`, `node_id` (nullable), `estimated_cpu_cores`/`estimated_ram_mb`, `source`, `placement_method` (`"platform_scheduler"`/`"ffd"`, P10-S2, added additively via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`), `placement_allowed`, `reason` (nullable), `dependency_status` (JSON), `decided_at`.

## Events

Published (stream `orchestration`): `orchestration.placement.decided` (`plugin_type`/`node_id`/`placement_allowed`/`source`/`placement_method`).
Consumed: none (no own NATS consumer — producer only, like `license-service`/`query-service`).

## Self-Registration

Like every other service, via `dms-registry-client` (3.2a).

## Tests

`services/plugin-orchestration-service/tests/` — 43 tests: `test_placement.py` (manifest source/median fallback/default fallback, staleness, singleton conflict, multi-node first-fit, load-profile ranking, platform scheduler delegation/fallback, dependency status), `test_sampler.py` (`psutil` values, upsert idempotence), `test_api.py` (gate, manifest CRUD, resource usage, node upsert gate, placement including `409`, `GET /nodes`/`GET /placements`), `test_platform_scheduler_kubernetes.py` (P24-S4, `KubernetesSchedulerAdapter` against a mocked `kubernetes` client: fitting node, no capacity → `None`, multi-node tie-break, unschedulable/not-ready filtering, millicore/binary unit parsing, label-selector pass-through).

## Open Points

- No real container automation (see "Boundaries of This Build-Out Stage") — remains deliberately open until a later session explicitly decides otherwise.
- No automatic coupling to `registry-service`'s drain mechanism (rebalancing today does not trigger a real drain call) — a deliberate scope boundary, see "Boundaries of This Build-Out Stage".
- `KubernetesSchedulerAdapter` (P24-S4) only checks `status.allocatable`, not the capacity actually consumed by running pods (no pod-usage-aware bin packing) — a documented simplification, see [ADR 0094](../adr/0094-plugin-orchestration-kubernetes-scheduler-adapter.md). Only in-cluster configuration is supported, no kubeconfig path for out-of-cluster use. Never tested against a real cluster (none available in this environment) — only against a mocked `kubernetes` client.
- Implicit profile derivation from historical observation not built (the concept explicitly names it as optional) — only explicitly declared `load_profile` values feed in.
- The service's own resource snapshot is a transitional solution until the full-fledged 10.1 sensor infrastructure (Phase 11).
- Rolling updates (reuse of the drain mechanism for update rollouts, expand/contract): P10-S3.
