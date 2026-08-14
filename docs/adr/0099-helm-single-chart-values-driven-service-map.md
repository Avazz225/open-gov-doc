# 0099 — Helm: one chart with a values-driven `services:` map instead of 34+ individual charts

**Status:** accepted (P26-S1, see `IMPLEMENTATION_PLAN.md`)
**Context:** Cross-concept (deployment/operations), affects `infra/k8s/dms/` (Phase 26, all containers from `infra/docker-compose.yml`)

## Decision

`infra/k8s/` gets exactly **one** Helm chart (`infra/k8s/dms/`, chart name `dms`) for all
~34 containers from `infra/docker-compose.yml` — not 34+ separate, nearly identical
charts/templates (one chart per service would have been the obvious but, given this high
degree of structural similarity, needlessly repetitive alternative). Instead:

- **One `services:` map in `values.yaml`**, one entry per container, with an identical
  shape for every service (`enabled`, `image.{repository,tag}`, `port`, `replicas`,
  `resources.{memory,cpu}.{requests,limits}`, `autoscaling.*`, `podDisruptionBudget.*`,
  `env`). A new service means a new map entry, never a new template.
- **Generic templates** (`templates/deployment.yaml`, `templates/service.yaml`,
  `templates/hpa.yaml`, `templates/pdb.yaml`), each iterating over
  `.Values.services` via `range` and emitting a manifest only for entries with
  `enabled: true` (plus, for HPA/PDB, `autoscaling.enabled`/`podDisruptionBudget.enabled`
  respectively).
- **`resources.baseline`** as a top-level default, which each service selectively
  overrides via `mergeOverwrite (deepCopy resources.baseline) (service.resources | default dict)`
  (only the specified fields, everything else stays at baseline).
- **`postgresql`/`keycloak`/`minio`** (only these three, see rationale) get an
  `enabled`/`external.*` toggle for "bundled vs. use an already-existing external
  instance" — `nats`/`redis` remain deliberately bundled-only without this toggle.
- **`storageService.targets`** as a native YAML list (the counterpart to
  `STORAGE_SERVICE_TARGETS`/`DMS_TARGETS`, see `BackendTargetConfig` in
  `services/storage-service/src/storage_service/settings.py`), serialized at runtime via
  `toJson` into the env var the service actually reads.

This session (P26-S1) builds the basic scaffolding plus four real example entries
(`registry-service`, `gateway-service`, `document-service`, `storage-service`) as proof
that templates + values structure work end-to-end (`helm lint`/`helm template`, see the
`docs/services/...` counterpart for this session in the report). The remaining ~28
services (P26-S2), the stateful infrastructure as real Deployments+PVC (P26-S3), the
storage replication CronJob (P26-S4), and the frontend apps (P26-S5) follow in the next
four sessions of this phase, each following the same pattern.

## Rationale

- **Why one chart instead of many**: the ~34 containers in `infra/docker-compose.yml`
  are structurally almost identical (FastAPI service, one port, DSN/NATS/registry URL as
  env vars, one Postgres schema) — 34 separate charts would have duplicated the same
  Deployment/Service/HPA/PDB boilerplate 34 times, with the usual follow-on problem that
  a later fix (e.g. a new standard env var or a security-context default) would need to
  touch 34 files instead of one. A values-driven map turns "a new service" into a pure
  data change.
- **Why still ONE chart and not, say, chart dependencies/subcharts per service**:
  subcharts would have reproduced the same boilerplate-multiplication effect (one
  `Chart.yaml` + templates folder per subchart) just one level deeper, without gaining
  the actual benefit (generic, values-parameterized templates).
- **Why `postgresql`/`keycloak`/`minio` get an external toggle, but `nats`/`redis`
  don't**: explicit user directive for this session (see session briefing P26-S1) —
  Postgres/Keycloak/MinIO are the three infrastructure components for which a real
  installation typically already has a managed external instance (cloud DB, central
  IAM, S3-compatible object storage), whereas NATS and Redis in this project have so far
  been run exclusively as chart-internal queue/cache instances with no known external
  replacement need.
- **Why `resources.baseline` + selective merge instead of writing out every service in
  full**: most of the ~34 services don't need individual requests/limits — only a small
  minority (e.g. `document-service` with file handling) need more. A baseline default
  with selective override keeps `values.yaml` concise for the common case, without
  limiting the ability to configure individual services differently.
- **Why `storageService.targets` as a native YAML list instead of continuing as a
  JSON free-text string**: `values.yaml` is this chart's central, versioned
  configuration location — a YAML-in-JSON-in-YAML string would be worse to read/review
  and more error-prone to edit than a native YAML list that the template only converts
  to the format expected by the service via `toJson` at render time.

## Consequences

- **`values.yaml` is the only place where new services appear in P26-S2..S5** — anyone
  building a new template instead of adding a new map entry is deviating from this
  decision and should justify that explicitly (e.g. a service with a structurally truly
  different deployment shape).
- **No real cluster deployment in Phase 26** — verification exclusively via
  `helm lint`/`helm template` (see concept reference, Phase 26 plan text). `helm`
  itself was not pre-installed in this development environment and was loaded as a
  static binary (`get.helm.sh`, v3.15.4) into the session scratchpad — no root/`apt`
  access needed, but also no persistent installation in the image; any future session
  that needs `helm` may have to repeat this or set up a more durable solution (e.g.
  `tools/`).
- **`postgresql.auth.password`/similar credentials currently sit as a plaintext
  value in `values.yaml`** (parity with the existing Compose dev setup, a deliberately
  documented v1 compromise) — P26-S3, which builds the real bundled Postgres/Keycloak/
  MinIO deployments, should replace this with a real Kubernetes `Secret` reference
  instead of carrying it forward unchanged.
- **`DMS_POSTGRES_DSN` in the `external` branch references `${DMS_POSTGRES_PASSWORD}`**
  as a placeholder rather than a real value (no plaintext password for a potentially
  production external DB in `values.yaml`) — this placeholder is not resolved by Helm
  and must be replaced by P26-S3 with a real secret-based mechanism (e.g.
  `envFrom`/`secretKeyRef`) before the external path is actually usable.
