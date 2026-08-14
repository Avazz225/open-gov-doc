# 0102 — Helm: frontend apps as `services:` entries, with a hard-guarded NEXT_PUBLIC_* build-time limitation

**Status:** accepted (P26-S5, see `IMPLEMENTATION_PLAN.md`)
**Context:** Concept 8, affects `infra/k8s/dms/` (Phase 26, continuation of [ADR 0099](0099-helm-single-chart-values-driven-service-map.md)/[ADR 0100](0100-helm-secrets-existing-secret-pattern.md)/[ADR 0101](0101-storage-cronjob-single-job-no-bulk-verify.md)), affects the 6 Next.js frontend apps under `apps/` ([ADR 0006](0006-user-ui-static-export-spa.md))

## Decision

The 6 Next.js frontend apps (`user-ui`, `admin-ui`, `process-designer`,
`reviewer-ui`, `migration-console`, `office-addin`) are managed as further
entries in `values.yaml`'s `services:` map and run through the same generic
`templates/deployment.yaml`/`service.yaml`/`hpa.yaml`/`pdb.yaml` templates as
the 32 stateless FastAPI services (no new template needed — from a k8s
perspective they too are "a container on a port," see ADR 0099). Two new
optional, generic fields make the structural differences explicit instead of
hiding them:

- **`healthCheckPath`** (default `"/healthz"`): liveness/readiness probe
  path. The 6 frontend apps set `"/"` (nginx serves only static files, no
  FastAPI health endpoint). As a side effect of this session, ALL 37 existing
  deployments get real probes for the first time — `templates/deployment.yaml`
  had NONE at all since P26-S1, even though every FastAPI service brings a
  `/healthz` endpoint per `docs/service-template.md` (see main.py of every
  service under `services/`) — a previously unnoticed gap, see
  "Consequences."
- **`staticFrontend: true`** (set only on the 6 frontend apps): makes
  `helm template`/`helm lint` fail hard as soon as `env:` contains a key
  with the `NEXT_PUBLIC_` prefix.

**The core decision that makes this guard necessary**: Next.js' static
export (`output: "export"`, ADR 0006) bakes `NEXT_PUBLIC_*` variables into
the JS bundle at `docker build` time (`apps/<name>/Dockerfile`: `ARG`/`ENV`
BEFORE `RUN npm run build`), not at container start — there is no Node
process at runtime that could read an environment variable (the
`nginx:1.27-alpine` stage only serves already-finished files). This chart
therefore **fundamentally cannot** set `NEXT_PUBLIC_*` values via
`values.yaml` or similar at deploy time — this is not an oversight, but a
real property of Next.js static export. Rather than pretending it's
configurable (an `env:` entry that renders without complaint in
`helm template` but has no effect in the running cluster), the `fail` guard
makes any attempt to do so immediately visible instead of leaving an
operator to discover it only after confusing live debugging.

**Production path**: every target environment with its own public gateway
address (`NEXT_PUBLIC_GATEWAY_BASE_URL`) needs its OWN image, built
beforehand via `docker build --build-arg NEXT_PUBLIC_GATEWAY_BASE_URL=... apps/<name>`,
whose `image.tag` encodes this target environment (e.g. `prod-eu-2026-08`
instead of `latest`) — BEFORE this chart references it. `values.yaml`'s
`image.repository`/`.tag` for the 6 frontend entries are therefore not pure
version numbers as with the 32 FastAPI services, but effectively
environment identifiers too.

## Rationale

- **`services:` map instead of a dedicated frontend template**: from a
  purely k8s perspective, the 6 apps are just as much "a container, a port,
  a Deployment+Service(+HPA/PDB)" as any FastAPI service — a separate
  template would have reproduced the boilerplate-multiplication effect
  explicitly avoided in ADR 0099, without a real structural reason (unlike,
  say, Postgres/Keycloak/MinIO/NATS/Redis, which need their own
  PVCs/secrets/healthchecks, see ADR 0099/0100). The two new optional
  fields (`healthCheckPath`/`staticFrontend`) suffice to capture the actual
  differences.
- **`fail` guard instead of a pure documentation warning**: a documentation
  warning alone would have risked the same silent failure as the
  `${DMS_POSTGRES_PASSWORD}` placeholder from P26-S1 (see ADR 0099/0100
  "Consequences") — syntactically valid, semantically ineffective, without
  any error message. A render-time `fail` is the only Helm-native way to
  make this specific failure case (NEXT_PUBLIC_* in `env:`) noticeable
  before the running cluster does.
- **`healthCheckPath` default `"/healthz"` instead of a required field**:
  keeps all 32 existing `values.yaml` entries unchanged (no migration step
  needed for P26-S1..S4's work) and fixes the probe gap for them
  automatically as a side effect — see "Consequences" for why this is
  handled in this session anyway rather than merely documented (small,
  low-risk, directly needed for the correctness of the frontend probes
  regardless).
- **`image.tag` as a de facto environment identifier instead of trying to
  "automate the problem away"**: conceivable alternatives (e.g. an init
  container that generates a `config.json` at runtime and the app
  loads it client-side) would have required a real code change across all
  6 apps (a new fetch call before actual app startup, its own configuration
  layer) — outside the scope of a pure Helm chart session (P26-S5 per
  `IMPLEMENTATION_PLAN.md` covers `infra/k8s/dms/`, not `apps/*` code) and
  would be incomplete without its own tests/ADR/doc updates for the
  affected apps. See "Consequences" for a design proposal, analogous to
  ADR 0101's proposal for a future bulk verify endpoint.

## Consequences

- **No `values.yaml` field controls the actual gateway address of a running
  frontend app** — this is a deliberately documented, unresolvable v1
  boundary of this chart, not an implementation oversight. An operator who
  wants to change the gateway address MUST rebuild the affected 6 images
  (new `--build-arg`), not just call `helm upgrade` with a new `--set`.
- **`services.gateway-service.ingress.host`** (see [ADR 0103](0103-helm-ingress-not-openshift-route.md))
  MUST match the `NEXT_PUBLIC_GATEWAY_BASE_URL` build arg with which the 6
  frontend images were built — no Helm mechanism in this chart can keep
  these in sync automatically, since the value is already baked into the
  image before `helm install`. An operator who changes `ingress.host`
  without rebuilding the frontend images gets no chart error
  (`helm template`/`lint` render without complaint), but rather
  cross-origin fetches that fail at runtime in the browser — a documented
  boundary, no automatable guard possible (the guard in this session
  covers only the ONE concretely checkable case: NEXT_PUBLIC_* in `env:`).
- **`office-addin` is one step more extreme**: `apps/office-addin/
  manifest.xml` contains hardcoded `https://localhost:3006` URLs
  (IconUrl/SupportUrl/AppDomain) — not even a build arg, but an XML file
  sitting in the source/image (see its own comment block,
  `docs/services/office-addin.md` "Open Points"). This chart cannot rewrite
  that at deploy time either — the same environment-image-build caveat as
  above, just one level earlier (source file instead of build arg).
- **All 37 existing deployments get real liveness/readiness probes as of
  this session** (previously none, see "Decision") — a gap that went
  unnoticed in P26-S1..S4, fixed here as a side effect of the
  `healthCheckPath` field already needed for the frontend apps, not as its
  own task for this session. Probe timing (`initialDelaySeconds`/
  `periodSeconds`/`failureThreshold`) is a generic compromise value not
  configurable per service (5s/10s/3 readiness, 15s/20s/3 liveness) —
  sufficient for this chart's lean FastAPI services and nginx frontends,
  but not a substitute for service-specific tuning should a future service
  start significantly slower (analogous to Keycloak's already individually
  tuned, more generous values in `templates/keycloak.yaml`).
- **Design proposal for a future session** (not part of this session,
  analogous to ADR 0101's proposal for storage-service): a runtime
  `/config.json` pattern (a small JSON file served by nginx that the app
  loads via `fetch` at startup, before it actually needs the gateway
  address) would replace `NEXT_PUBLIC_GATEWAY_BASE_URL` with a real value
  injectable at runtime via a `ConfigMap`+volume mount — but would require
  a code change across all 6 apps (`apps/*/src`) plus their own
  tests/ADR/doc updates, no longer a pure Helm chart building block.
