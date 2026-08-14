# 0103 — Helm: vanilla `Ingress` instead of OpenShift `Route`, host-based routing

**Status:** accepted (P26-S5, see `IMPLEMENTATION_PLAN.md`)
**Context:** Concept 8/3.5, affects `infra/k8s/dms/` (Phase 26 — "Helm charts for k8s/**OCP**," continuation of [ADR 0099](0099-helm-single-chart-values-driven-service-map.md)/[ADR 0102](0102-helm-frontend-apps-buildtime-env-limitation.md)), fulfills the point left open since P26-S1 in `values.yaml`'s `gateway-service` comment ("real OCP route/ingress follows in P26-S2/S5")

## Decision

`templates/ingress.yaml` (new, P26-S5) renders a `networking.k8s.io/v1`
**`Ingress`** (vanilla Kubernetes) for each entry in `.Values.services` with
`ingress.enabled: true` — used by `gateway-service` as well as the 6
frontend apps (7 outbound public routes total in this session). There is
**no** native OpenShift `Route` object in this chart.

Each entry gets its **own hostname** (`ingress.host`, e.g.
`user-ui.dms.local`), **not** a shared host with path-prefix routing (e.g.
`dms.local/user-ui`).

## Rationale

- **Ingress instead of Route, even though the phase is explicitly called
  "k8s/OCP"**: OpenShift's standard router (HAProxy-based) has natively
  accepted regular `networking.k8s.io/v1` `Ingress` objects for several OCP
  versions (implemented internally via a Route adapter) — a single
  `Ingress` template thus covers both vanilla Kubernetes clusters and
  OpenShift clusters, without needing to maintain two parallel sets of
  templates (the same DRY principle as in ADR 0099: no unnecessary
  duplication where a common denominator suffices). A native `Route`
  object would additionally offer OCP-specific capabilities (e.g.
  `haproxy.router.openshift.io/*` annotations, native
  edge/passthrough/reencrypt TLS termination modes, wildcard routes
  without an explicit DNS entry per host) — for the needs of this phase
  (public HTTP(S) access to 6 static frontend apps + one API gateway) the
  `Ingress` common denominator is fully sufficient.
- **A deliberate v1 scope cut, not an overlooked part of the phase name**:
  an additional `templates/route.yaml` (OpenShift `route.openshift.io/v1`)
  would be a purely additive extension — the same `services:` map, the
  same `ingress.*` fields, just a second template with a different
  `apiVersion`/`kind` and possibly annotation handling for route-specific
  extras. Deliberately deferred for this session (see "Consequences")
  rather than shipping it untested/unverified — this chart was verified
  exclusively against `helm lint`/`helm template` (no real OCP cluster
  available in this development environment, see ADR 0099 "no real
  cluster deployment required in Phase 26"), and a route building block
  actually tested on a real OCP router could not be credibly claimed as
  "verified" under this constraint anyway.
- **Host-based instead of path-prefix-based**: checked (`grep basePath
  apps/*/next.config.mjs`, no hits) — none of the 6 Next.js static
  exports set `basePath`/`assetPrefix`. Their `/_next/...` asset paths are
  therefore root-relative; multiple apps under the same host with pure
  path-prefix routing (e.g. `dms.local/admin-ui/`) would overwrite each
  other's asset URLs (each app would try to load `/_next/...` at the host
  root, not under its own prefix) — an `Ingress` `path` rewrite alone
  doesn't solve this; it would additionally need a `next.config.mjs`
  change (setting `basePath`) AND a rebuild of all 6 images. Host-based
  routing needs no app-side change and works unchanged with the existing
  Next.js export.
- **`ingress.host` values are dev/demo placeholders** (`*.dms.local`,
  analogous to `global.installationId: "local-dev"` elsewhere in
  `values.yaml`) — deliberately configurable per service in `values.yaml`
  (no hardcoded value in the template), so a real installation can replace
  them with real DNS names without a chart change (`--set
  services.user-ui.ingress.host=...` or a dedicated values overlay file).
- **`office-addin` with `tls.enabled: true` as the sole exception among the
  6 apps**: `docs/services/office-addin.md` documents a hard HTTPS
  requirement (Office generally only loads add-in web content over HTTPS,
  aside from a few local development exceptions) — the remaining 5 apps
  and `gateway-service` have `tls.enabled: false` as the default
  (functional even without TLS; a production deployment would typically
  enable it anyway, but it's not a hard prerequisite here as it is for
  `office-addin`).
- **No cert-manager integration/automatic certificate issuance**:
  `ingress.tls.secretName` expects an already-existing TLS secret; this
  chart doesn't create one. `ingress.annotations` is available as a free
  field through which an operator can add, e.g.,
  `cert-manager.io/cluster-issuer` themselves, without this chart assuming
  or bundling a particular certificate solution — the same
  operator-agnostic principle as with `existingSecret` (ADR 0100).

## Consequences

- An operator who needs OCP-**native** route capabilities (e.g.
  wildcard-subdomain routing without an explicit DNS entry per host,
  HAProxy annotations for sticky sessions/timeouts, native
  passthrough TLS termination) must either create their own `Route`
  objects managed outside this chart (referencing the same
  `<fullname>-<service>` Kubernetes services that `templates/service.yaml`
  already renders — no chart rework needed, just an additional,
  separately maintained manifest), or wait for a future chart extension
  (`templates/route.yaml`, the same `services.<name>.ingress` fields as
  here). A documented gap, not a silent one.
- `services.gateway-service.ingress.host` MUST match the
  `NEXT_PUBLIC_GATEWAY_BASE_URL` build arg with which the 6 frontend
  images were built (see ADR 0102 "Consequences") — changing
  `ingress.host` alone (without an image rebuild) does not automatically
  make the frontend apps functional again.
- `gateway-service.env.DMS_CORS_ALLOWED_ORIGINS` must be kept in sync by
  hand with the 6 `ingress.host` values (`values.yaml` currently lists all
  6 `https://<app>.dms.local` placeholders plus the existing
  `http://localhost:3000` Compose dev case) — no Helm templating derives
  one from the other automatically (both values sit in the same
  `values.yaml` file, but `DMS_CORS_ALLOWED_ORIGINS` is a raw JSON string
  within `services.gateway-service.env`, not a structured field from which
  a cross-reference to the 6 other `services.<name>.ingress.host` values
  could be cleanly derived, without breaking the generic `env:` mechanism
  for this one special case).
