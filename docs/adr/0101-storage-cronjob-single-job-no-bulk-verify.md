# 0101 — Storage CronJob: only ONE CronJob (replication), no second one for fixity verification

**Status:** accepted (P26-S4, see `IMPLEMENTATION_PLAN.md`)
**Context:** Concept 3.6, affects `infra/k8s/dms/` (Phase 26, continuation of [ADR 0099](0099-helm-single-chart-values-driven-service-map.md)/[ADR 0100](0100-helm-secrets-existing-secret-pattern.md)), fulfills the external carrier for `storage-service`'s on-demand endpoints announced in [ADR 0004](0004-storage-redundancy-scope.md) and PROGRESS.md P20-S6.

## Decision

The new `templates/storage-cronjob.yaml` renders **exactly one** `CronJob`
(`storageCronJob.enabled`), which periodically calls `POST /replication/process-pending`
against `storage-service`'s in-cluster service DNS
(`http://<fullname>-storage-service:8000`, via the new `dms.storageServiceBaseUrl`
helper, the same URL formula as `dms.dependsOnServicesEnv`). There is deliberately
**no** second CronJob for the periodic object fixity verification assumed in the
session briefing.

## Rationale

**The real verification endpoint is not a bulk endpoint.** The session briefing
assumed (with an explicit verification mandate) an endpoint "something like
`/object-verify/.../all`" that could periodically check "all objects." The actual
code (`services/storage-service/src/storage_service/main.py` lines 523-542,
`@app.get("/object-verify/{key:path}/all")`) instead verifies **all configured
targets/copies of ONE single object `key` passed as a path parameter** — not all
objects present in the store. This matches its own docs
(`docs/services/storage-service.md`: "fixity check across **all** configured
targets" — "targets" refers to an object's redundancy targets, not the set of
objects) as well as the "Open Points" section of the same file, which lists "no
automatic periodic execution of `/object-verify/.../all`" as a known gap without
promising an enumeration mechanism.

`storage-service` currently has **no** endpoint that lists object keys or returns a
batch of objects not yet verified (or verified longest ago) — unlike the replication
retry queue (`repository.list_pending_copies`/`POST /replication/process-pending`,
since ADR 0082 with full-jitter backoff via `ObjectCopy.next_retry_at`), fixity has
no counterpart to `next_retry_at`. `ObjectMetadata` (models.py) carries no
`last_verified_at`/`next_verify_at` field, `repository.py` has no
`list_unverified`-style query.

A CronJob can only call what the API actually offers from the outside. Building a
periodic "verify everything" job against an endpoint that necessarily requires a
concrete, known-in-advance `key` would either be non-functional (a fixed placeholder
key, forever verifying only a single object) or would require object enumeration from
the outside (e.g. via another service with knowledge of all storage keys, with
pagination/error handling in a plain curl shell script) — both would dilute the
actual functional requirement (regular fixity check of **all** copies, Concept 3.6)
in a way that would suggest it works completely when it does not. Adding a new bulk
endpoint directly to `storage-service` would be the clean solution, but is outside
the scope of this Helm chart session (P26-S4 per `IMPLEMENTATION_PLAN.md` covers
`infra/k8s/dms/`, not service code) and would be incomplete without its own
tests/ADR/doc update for `storage-service` itself.

**Auth**: neither `/replication/process-pending` nor `/object-verify/{key}/all`
currently require an auth header (no `Header(...)`/`Depends(...)` gate in
`main.py`, unlike e.g. `DELETE /objects/{key}`'s optional `X-DMS-Roles` governance
bypass). The CronJob nonetheless sends `X-DMS-Principal: system:storage-replication-cronjob`
along (the same pattern as `archival-service`'s `_SYSTEM_PRINCIPAL_HEADERS`/
`workflow-service`'s `X-DMS-Principal` check for other service-to-service calls) —
costs nothing, keeps the call consistent with the project-wide pattern, and makes
it identifiable as a machine call in logs, even if `storage-service` later gates
this endpoint.

**Utility image**: `curlimages/curl:8.10.1` instead of a full `storage-service`
image — the container does nothing more than a single HTTP POST. No existing
project convention for a utility image was found (`infra/docker-compose.yml` uses
curl/wget only within the respective service images for healthchecks), so the
common official minimal image was chosen.

## Consequences

- Secondary copies are now actually caught up automatically as of this session
  (`POST /replication/process-pending` every 15 minutes, default
  `storageCronJob.replication.schedule`) — the "external carrier" left open in
  ADR 0004/PROGRESS.md P20-S6 is now actually present.
- Regular fixity verification of **all** objects remains a manual/on-demand
  operation (`GET /object-verify/{key}/all` per object) — Concept 3.6's "regular
  fixity check across all copies" is thus NOT fully satisfied for Phase 26.
  `values.yaml`'s `storageCronJob.verification.enabled: false` is a deliberately
  unwired placeholder (no template reads it) for a later session.
- **Recommended shape for a future solution** (not part of this session):
  `storage-service` gets, analogous to the replication retry queue, a new field
  `ObjectMetadata.next_verify_at` (or its own `verification_schedule` table) plus
  a new endpoint `POST /object-verify/process-pending?limit=N`, which selects the
  `N` objects verified longest ago, calls `verify_all_copies` per object, and
  resets `next_verify_at` at a fixed interval (no retry backoff needed, since
  there is no failure case in the ADR-0082 sense) — mirroring
  `list_pending_copies`/`process_pending`. Only then can a second, real CronJob
  analogous to this one be built.
- Should `storage-service` later add real auth gating to these two endpoints
  (`X-DMS-Principal`/`X-DMS-Roles` requirement), this CronJob continues to work
  unchanged, since the principal header is already being sent.
