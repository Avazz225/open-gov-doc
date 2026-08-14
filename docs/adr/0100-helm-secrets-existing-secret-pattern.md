# 0100 — Helm: Postgres/Keycloak/MinIO passwords via the `existingSecret` pattern instead of plaintext env

**Status:** accepted (P26-S3, see `IMPLEMENTATION_PLAN.md`)
**Context:** Cross-concept (deployment/operations), affects `infra/k8s/dms/` (Phase 26, continuation of [ADR 0099](0099-helm-single-chart-values-driven-service-map.md))

## Decision

The three stateful infrastructure components with an admin password
(Postgres, Keycloak, MinIO — NATS/Redis run without auth in this chart,
see ADR 0099) each get a `<component>.existingSecret` field in
`values.yaml`, following the pattern established in the Helm world of
"existingSecret-if-set-else-generate" (see e.g. Bitnami charts):

- **Empty (default)**: `templates/secrets.yaml` generates a Kubernetes
  `Secret` from the plaintext `*.auth.password`/`*.admin.password` value
  that has already been in `values.yaml` since P26-S1 (dev/test
  convenience, parity with the existing Compose dev setup) — explicitly
  NOT intended for production, documented as such already in ADR 0099.
- **Set**: the referenced, already-existing secret is used instead;
  `templates/secrets.yaml` generates NOTHING for it (avoids the classic
  Helm anti-pattern of an unused, orphaned generated secret alongside the
  real one). Production path: either `--set postgresql.existingSecret=...`
  together with a separate values file NOT stored in the repo containing
  real passwords, or a secret created beforehand via External Secrets
  Operator/Sealed Secrets/manually.
- For the `external` branch (Postgres/MinIO with `enabled: false`),
  `<component>.external.existingSecret` MUST be set — this chart naturally
  has no knowledge of the password of an already-existing external
  instance and generates nothing for it.
- Each of the three deployments (the bundled Postgres/Keycloak/MinIO
  themselves) as well as each of the 32 stateless services
  (`DMS_POSTGRES_PASSWORD`) consume the password exclusively via
  `valueFrom.secretKeyRef`, never again as a literal `value:` env entry.
- `DMS_POSTGRES_DSN` (a composite connection string, not a single field)
  does NOT reference the password directly via `secretKeyRef` (technically
  not possible — a `secretKeyRef` always supplies the complete value of a
  single env entry, not a substring), but via native Kubernetes env-var
  reference substitution: `DMS_POSTGRES_PASSWORD` is set first via
  `secretKeyRef`, then `DMS_POSTGRES_DSN` builds it in afterward within
  the same container `env` array via `$(DMS_POSTGRES_PASSWORD)` —
  Kubernetes resolves `$(VAR_NAME)` at pod-start time when the referenced
  variable appears earlier in the same `env` array (an officially
  documented k8s pattern for exactly this case). Replaces the
  `${DMS_POSTGRES_PASSWORD}` placeholder from P26-S1 (curly braces — never
  resolved by Helm AND not recognized as a reference by Kubernetes, a
  pure documentation stand-in with no function) with an actually working
  mechanism. `KC_DB_PASSWORD` in the bundled Keycloak deployment uses the
  same Postgres secret reference as `DMS_POSTGRES_PASSWORD` (Keycloak uses
  the same Postgres instance, see `infra/docker-compose.yml`).

## Rationale

- **The `existingSecret` pattern instead of e.g. `sealed-secrets`/
  `external-secrets` as a chart dependency**: this chart should remain
  installable in as many cluster environments as possible without
  requiring an additional operator prerequisite (see ADR 0099 "no real
  cluster deployment required in Phase 26" — the target environment is
  unknown at this point). The `existingSecret` field is operator-agnostic:
  an operator can create the referenced secret beforehand using ANY
  mechanism of their choice (even a simple `kubectl create secret` for
  small installations), without this chart assuming or bundling a
  particular solution.
- **Why a generated plaintext default remains despite this, instead of
  making `existingSecret` mandatory**: an immediately runnable
  `helm install` without a prior manual secret-creation step remains
  important for the dev/learning/demo use of this chart (analogous to the
  existing `docker-compose.yml` dev setup) — exactly the compromise
  already documented in ADR 0099, here only extended with a real secret
  mechanism instead of a raw plaintext env value.
- **`$(VAR_NAME)` substitution instead of a full "dsn" secret key**: an
  alternative would have been to store the entire composite DSN string
  (including password) itself as the secret value. Rejected because, for
  the `external` branch, that would mean the chart would need to know the
  entire string (including host/port/database/username), even though
  these fields already sit individually in `values.yaml` (not secret) —
  only the password needs protection. The `$(VAR_NAME)` substitution
  cleanly separates: secret fragments (password) come from the Secret,
  everything else remains an ordinary Helm template output computed from
  `values.yaml`.
- **No `required` guard on `external.existingSecret`**: a `required`
  directive would have made `helm template`/`helm install` fail hard as
  soon as `enabled: false` is set without `external.existingSecret` also
  being set — that would have caused the toggle test required in this
  session (`--set postgresql.enabled=false --set
  postgresql.external.host=...` WITHOUT `existingSecret`) to fail.
  Deliberately not enforced, in favor of testability/robustness; an empty
  secret name renders into a `secretKeyRef` with an empty `name:`, which
  would visibly fail on a real `kubectl apply` (not a silent failure) —
  a `required` directive or a `NOTES.txt` warning could be retrofitted by
  a later session addressing real cluster deployments.

## Consequences

- `values.yaml` remains the only place in the repo with plaintext dev
  passwords — but now only as input to a generated secret, no longer
  directly visible as a pod env value (e.g. in `kubectl describe pod`
  output, which shows `value:` fields, whereas `valueFrom.secretKeyRef`
  fields show only the secret/key name, not the value itself).
- Switching from a generated secret to `existingSecret` (or vice versa)
  changes the secret *name* a deployment references — a running
  deployment only notices this on the next pod restart (no automatic
  secret hot-reload in Kubernetes, independent of this chart).
- `infra/k8s/dms/files/postgres-init/001-schemas.sql` (ConfigMap content
  for the bundled Postgres, creates the `keycloak` schema) is a manually
  maintained copy of `infra/postgres-init/001-schemas.sql` — Helm's
  `.Files.Glob` can only read within the chart directory, a reference to
  the Compose path is not possible. Future sessions that add further
  `CREATE SCHEMA` lines to `infra/postgres-init/` must maintain the copy
  under `infra/k8s/dms/files/postgres-init/` alongside it.
- `storageService.targets[].endpoint_url` (secondary S3 target) has since
  also used a placeholder replaced by `dms.storageServiceTargetsEnv`
  (`__DMS_MINIO_ENDPOINT__` instead of the hardcoded Compose hostname
  `minio`) — not a direct part of this ADR decision, but from the same
  session context (a real `templates/minio.yaml` only makes this
  previously latent bug, present since P26-S1, visible/relevant). The
  `access_key`/`secret_key` fields of the same target deliberately remain
  plaintext dev values — a secret integration for this storage-service-
  specific target list is an open point for a later session.
