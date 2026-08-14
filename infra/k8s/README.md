# infra/k8s/

Helm chart for optional k8s/OCP operation of the DMS (Phase 26, see
`IMPLEMENTATION_PLAN.md` and [ADR 0099](../../docs/adr/0099-helm-single-chart-values-driven-service-map.md)).
Local development still runs via `../docker-compose.yml` — this chart
is an additional, alternative deployment path for a real Kubernetes/
OpenShift cluster, not a replacement for Compose in development.

## Structure

```
infra/k8s/dms/
├── Chart.yaml
├── values.yaml          # central configuration — see conventions below
├── .helmignore
├── files/
│   └── postgres-init/
│       └── 001-schemas.sql  # copy of infra/postgres-init/ (see comment there)
└── templates/
    ├── _helpers.tpl      # naming/label helpers + generic env/resource building blocks
    ├── deployment.yaml    # ONE template for ALL services: entries (range)
    ├── service.yaml       # ditto
    ├── hpa.yaml            # HorizontalPodAutoscaler, only where autoscaling.enabled
    ├── pdb.yaml             # PodDisruptionBudget, only where podDisruptionBudget.enabled
    ├── secrets.yaml         # Postgres/Keycloak/MinIO admin secrets (P26-S3, ADR 0100)
    ├── postgresql.yaml      # bundled Postgres: ConfigMap+PVC+Deployment+Service (P26-S3)
    ├── keycloak.yaml        # bundled Keycloak: Deployment+Service (P26-S3)
    ├── minio.yaml           # bundled MinIO: PVC+Deployment+Service (P26-S3)
    ├── nats.yaml            # bundled NATS (bundled-only): PVC+Deployment+Service (P26-S3)
    ├── redis.yaml           # bundled Redis (bundled-only): Deployment+Service (P26-S3)
    ├── storage-cronjob.yaml  # CronJob: external trigger for storage-service replication (P26-S4, ADR 0101)
    ├── ingress.yaml          # Ingress per services.<name>.ingress.enabled (P26-S5, ADR 0103)
    └── NOTES.txt
```

The five stateful infrastructure components (`postgresql.yaml` /
`keycloak.yaml` / `minio.yaml` / `nats.yaml` / `redis.yaml`) deliberately
have their own templates instead of going through the generic `services:`
mechanism — volumes, differing image sources/ports/health checks, and
secret references do not structurally fit the generic schema built for
stateless FastAPI services (see ADR 0099/0100).

The 6 Next.js frontend apps (`user-ui`/`admin-ui`/`process-designer`/
`reviewer-ui`/`migration-console`/`office-addin`, P26-S5), by contrast, run
EXACTLY through the generic `services:` mechanism like any FastAPI service
— from a k8s perspective they are structurally the same ("one container, one
port"); see ADR 0102 for the adjustments that are nonetheless required
(`healthCheckPath`, `staticFrontend` guard).

**One** chart for all containers from `infra/docker-compose.yml`, not one
chart/template per service — see ADR 0099 for the rationale. A new
service means a new entry under `services:` in `values.yaml`, not a
new template.

## Verifying

No real cluster deployment is required in Phase 26 — it is sufficient to run:

```bash
helm lint infra/k8s/dms
helm template my-release infra/k8s/dms
```

`helm template ... | kubectl apply --dry-run=client -f -` is an optional
extra check if `kubectl` is available — not a prerequisite.

If `helm` is not preinstalled in the given environment: download it as a
static binary from <https://get.helm.sh> (no root/`apt` needed), e.g.:

```bash
curl -sSL -o helm.tar.gz https://get.helm.sh/helm-v3.15.4-linux-amd64.tar.gz
tar xzf helm.tar.gz
./linux-amd64/helm lint infra/k8s/dms
```

## `values.yaml` conventions

- **`services.<name>`**: one entry per container, identical shape for
  every service (`enabled`, `image.{repository,tag}`, `port`, `replicas`,
  `resources`, `autoscaling`, `podDisruptionBudget`, `dependsOnServices`,
  `env`). For field details/descriptions see the comment block at the
  top of `values.yaml`.
- **`resources.baseline`**: default requests/limits for all services
  without their own `resources` block. A service with its own (even
  partial) `resources` block selectively overrides it (deep merge in
  `templates/deployment.yaml`).
- **`postgresql` / `keycloak` / `minio`**: `enabled: true` = instance
  bundled in the chart (real Deployments+Service(+PVC) since P26-S3, see
  `templates/postgresql.yaml`/`keycloak.yaml`/`minio.yaml`), `enabled: false`
  + `external.*` = use an already-existing external instance (for Postgres/
  MinIO additionally `external.existingSecret` — a required field, see
  below). `nats`/`redis` are deliberately bundled-only, without this toggle.
- **`<component>.existingSecret`** (P26-S3, ADR 0100): standard Helm
  convention "existingSecret-if-set-else-generate" for Postgres/Keycloak/
  MinIO admin passwords. Empty (default) = `templates/secrets.yaml` generates
  a secret from the plaintext `*.auth.password`/`*.admin.password` value in
  `values.yaml` (dev/test convenience, NOT for production). Set = reference
  this already-existing secret, nothing is generated — in both cases, the
  Deployments consume the password exclusively via
  `valueFrom.secretKeyRef`, never as a literal env value. `DMS_POSTGRES_DSN`
  builds the password into the connection string via native Kubernetes
  `$(VAR_NAME)` substitution (see the `dms.postgresDsn` comment in
  `templates/_helpers.tpl`).
- **`storageService.targets`**: native YAML list (counterpart to
  `BackendTargetConfig` in `services/storage-service`), serialized at
  runtime via `toJson` into the `DMS_TARGETS` env var read by the service
  (only for services with `storageTargetsEnv: true`, currently
  `storage-service`). Since P26-S3, the `secondary-s3` target uses the
  placeholder `__DMS_MINIO_ENDPOINT__` instead of a hardcoded Compose
  hostname (respects `minio.enabled` bundled/external, see
  `dms.storageServiceTargetsEnv`).
- **`services.<name>.usesRedis`** (P26-S3, analogous to `usesKeycloak`):
  injects `DMS_REDIS_URL` (bundled-only, `dms.redisUrl` helper) — currently
  only set for `gateway-service` (rate limiting, ADR 0097).
- **`storageCronJob`** (P26-S4, ADR 0101): `enabled` controls whether
  `templates/storage-cronjob.yaml` renders a `CronJob` that periodically
  calls `POST /replication/process-pending` against `storage-service`'s
  in-cluster service DNS (`dms.storageServiceBaseUrl` helper, same
  URL formula as `dms.dependsOnServicesEnv`) — the external carrier for the
  replication retry queue announced in ADR 0004/PROGRESS.md P20-S6.
  `replication.schedule`/`.limit`/`.activeDeadlineSeconds`/
  `.{successful,failed}JobsHistoryLimit` are configurable, `image.*`
  selects a lightweight `curlimages/curl` utility image instead of the
  full `storage-service` image. `principalHeader` sets an
  `X-DMS-Principal` header (service-to-service convention, currently not
  enforced by `storage-service`, see ADR 0101). `verification.enabled`
  is a deliberately UNWIRED placeholder (no template uses it) — the
  real `GET /object-verify/{key}/all` endpoint only verifies a
  single object passed via a path parameter; no bulk/listing
  endpoint for "all objects" currently exists in `storage-service` (see
  ADR 0101 for the full rationale and a design proposal for a
  future session).
- **`services.<name>.healthCheckPath`** (P26-S5): liveness/readiness probe
  path, default `"/healthz"` (every FastAPI service in this project has
  this endpoint per `docs/service-template.md`). The 6 frontend apps
  set `"/"` (nginx only serves static files, no
  FastAPI health endpoint) — see ADR 0102.
- **`services.<name>.staticFrontend`** (P26-S5, only set for the 6
  frontend apps): activates a Helm `fail` guard in `templates/deployment.yaml`
  that hard-aborts `helm template`/`lint` as soon as `env:` contains a
  `NEXT_PUBLIC_*` key — these Next.js build-time variables are
  baked into the JS bundle during `docker build`; a
  container env value at runtime would have no effect (see ADR 0102 for
  the full rationale of this structural Next.js static-export
  limitation and the recommended production path — a
  dedicated image per target environment, prebuilt with the appropriate
  `--build-arg`).
- **`services.<name>.ingress`** (P26-S5, ADR 0103): optional
  public access path via a vanilla `Ingress` object
  (`networking.k8s.io/v1`, not an OpenShift `Route`, see ADR 0103 for
  the rationale — OCP's default router natively accepts regular
  `Ingress` objects). Shape: `{enabled, className, host, path, pathType, tls:
  {enabled, secretName}, annotations}`. Active on `gateway-service` and
  all 6 frontend apps, each with its own hostname (host-based rather than
  path-prefix-based routing — none of the 6 `next.config.mjs` set
  `basePath`, see ADR 0103). `office-addin` is the only app with
  `tls.enabled: true` as default (HTTPS is mandatory for Office add-ins, see
  `docs/services/office-addin.md`).

## Status

P26-S1 (basic scaffolding + 4 example services: `registry-service`,
`gateway-service`, `document-service`, `storage-service`). P26-S2 added the
remaining 28 stateless FastAPI services from `infra/docker-compose.yml`
(`services:` now has 32 entries) — same templates, no new ones. Autoscaling,
in addition to `document-service` (P26-S1), is active for
`virus-scan-service`, `rendering-service`, `ocr-service`, and
`search-service` (rationale in the P26-S2 session report/
`PROGRESS.md`); PDB, in addition to `gateway-service`/`document-service`, for
`virus-scan-service`, `rendering-service`, `search-service`.
Since P26-S2, `templates/_helpers.tpl`'s `dms.dependsOnServicesEnv` also
supports `{name, envVar}` entries alongside plain service names, for the
small minority of services whose `infra/docker-compose.yml` env var name
deviates from the standard pattern `DMS_<KEY>_BASE_URL` (see `auth-service`/
`workflow-service` in `values.yaml`).

P26-S3 built the stateful infrastructure (Postgres/Keycloak/MinIO/
NATS/Redis) as real Deployments+Service(+PVC) (see structure/
conventions above) and closed the secrets gap documented since P26-S1
(ADR 0100). `helm template` now renders 37 Deployments (32 stateless
services + 5 infrastructure components), 3 PersistentVolumeClaims (Postgres/
MinIO/NATS — Keycloak/Redis deliberately without, see the respective
`values.yaml` comment), and 3 generated Secrets in the default case.

P26-S4 added the storage replication CronJob (`templates/
storage-cronjob.yaml`, see ADR 0101) — `helm template` now additionally
renders 1 `CronJob` (37 Deployments/Services etc. unchanged). Deliberately
only ONE CronJob instead of the two assumed in the phase briefing: the real
verification endpoint (`GET /object-verify/{key}/all`) requires a
concrete object `key` and cannot be called blindly, periodically "for all
objects" — see ADR 0101 for details and a proposal for a
future `storage-service` extension that would retrofit this.

**P26-S5 (final session of this phase)** added the 6 Next.js frontend apps
(`user-ui`/`admin-ui`/`process-designer`/`reviewer-ui`/`migration-console`/
`office-addin`) as further `services:` entries (see ADR 0102 for
the build-time vs. runtime configuration boundary of these statically
exported apps) and built real `Ingress` resources for these 6 apps plus
`gateway-service` (`templates/ingress.yaml`, ADR 0103 — vanilla
`Ingress` instead of an OpenShift `Route` object, host-based routing).
As a side effect, ALL 43 Deployments in this chart got real
liveness/readiness probes for the first time (`healthCheckPath`, default
`/healthz`, frontend apps `/`) — a gap that had gone unnoticed since P26-S1
(`templates/deployment.yaml` previously had no probes at all, despite the
`/healthz` endpoint on every FastAPI service), see ADR 0102 "Decision"/
"Consequences".

`helm template` now renders **43 Deployments** (37 before + 6
frontend apps), **43 Services**, **7 PodDisruptionBudgets** (5 before +
`user-ui`/`admin-ui`), **7 Ingress** (`gateway-service` + 6 frontend apps,
0 before — an entirely new resource type this session), **5
HorizontalPodAutoscaler** (unchanged — the 6 frontend apps do not use
autoscaling), 1 `CronJob`, 3 `PersistentVolumeClaim`, 3 generated `Secret`
— **113 manifests** in total in the default case (92 before, i.e. +21: +6
Deployment, +6 Service, +2 PDB, +7 Ingress). Phase 26 is thus fully
complete.

**Known open points for a hypothetical future session** (see
ADR 0102/0103 "Consequences" for details): no runtime mechanism that could
still change `NEXT_PUBLIC_*` values after the image build (a structural
Next.js static-export boundary, not an implementation oversight); no
native OpenShift `Route` template (a deliberate v1 scope cut — `Ingress`
also works on OCP); `office-addin`'s `manifest.xml` contains hardcoded
`https://localhost:3006` URLs that this chart cannot rewrite;
`gateway-service.env.DMS_CORS_ALLOWED_ORIGINS` must be kept manually in
sync with the 6 `ingress.host` values (no automatic cross-referencing
between the two `values.yaml` locations).
