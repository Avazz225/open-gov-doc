# 0073 — OCR/Rendering/Virus-Scan RBAC

**Status:** accepted (Session 8 of 11, see Phase 19 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap Phase 19 Session 8, affects `virus-scan-service`, `ocr-service`,
`rendering-service`, `permission-service`, and the HTTP clients of `rendering-service`/`search-service`/
`archival-service`

## Decision

Three gaps of different kinds:

1. **`virus-scan-service`'s quarantine area (`GET /scans?status=infected`, `POST /scans/{id}/release`,
   `POST /scans/{id}/purge`) previously only checked a plain `X-DMS-Roles` string-equality gate**
   (`_has_quarantine_role`, `settings.quarantine_admin_role`, default `"dms-admin"`) — no real
   permission-service call. Replaced (not supplemented) with `_require_quarantine_permission`: checks
   `admin.quarantine` via `PermissionServiceClient.has_permission`. New domain admin role
   `domain-admin-virus-scan` (`services/permission-service/src/permission_service/repository.py`,
   `DOMAIN_ADMIN_ROLES`) carries this permission. `quarantine_admin_role`/`DMS_QUARANTINE_ADMIN_ROLE`
   were removed without replacement.
2. **`ocr-service` and `rendering-service` had NO permission check at all.** New
   `_require_ocr_permission`/`_require_rendering_permission` helpers (identical pattern to ADR 0072):
   `ocr.read`/`ocr.write` and `rendering.read`/`rendering.write` respectively on the root resource
   (`root`).
3. **Four real consumers retrofitted** — `GET /ocr-results`/`GET /renditions`/`.../content` are called
   from NATS consumer contexts without a human principal: `rendering-service`'s `OcrServiceClient`,
   `search-service`'s `OcrServiceClient` and `RenderingServiceClient`, `archival-service`'s
   `RenderingClient` — all four now receive a synthetic `X-DMS-Principal: system:<Service>` header
   (established pattern from ADR 0070).

## Rationale

- **Why `admin.quarantine` REPLACES rather than SUPPLEMENTS (unlike ADR 0072's `archive_retrieval_role`)**:
  the roadmap mandate for this session literally said "raised ... to a real permission-service check" —
  a raise, not an addition. Unlike archival-service's `archive_retrieval_role` (which remains a control
  independently named in Concept 5.6), `quarantine_admin_role` was from the start only a placeholder
  mechanism (a plain string comparison against an unverified header), not a standalone, conceptually
  anchored second gate.
- **Why a NEW domain admin role instead of extending the "everyone" group**: Concept 2.5 literally
  names quarantine access as *"its own, tightly scoped role"* — unlike archival-service/
  reporting-service (P19-S7) or case-service (P19-S5), this area was ALREADY a real permission
  restricted to `dms-admin` before this session, not a previously de-facto open gap. The "everyone"
  extension (ADR 0067) serves to preserve previously OPEN behavior — here the opposite applies: an
  already closed door stays closed, only the key mechanism changes from a role string to a real
  permission-service role.
- **Why `ocr.read`/`.write`/`rendering.read`/`.write` AND the "everyone" extension for them**: both
  services previously had no check whatsoever — exactly the pattern from P19-S7 (archival/reporting),
  "everyone" receives the previous de-facto open behavior.
- **`document-service`'s independent `quarantine_release_admin_role` gate remains unchanged**: the
  `POST /documents/from-quarantine-release` check there (`_has_quarantine_release_role`, its own
  setting, coincidentally also defaulting to `"dms-admin"`) is a SEPARATE mechanism in a different
  service, outside this session's scope — `virus-scan-service`'s `release_scan` still passes
  `x_dms_roles` through unchanged, even though its own RBAC check now runs independently of it.

## Consequences

- **Tests**: `virus-scan-service` 32 (role-string tests replaced with RBAC positive/negative tests, new
  `_grant_quarantine_permission` session fixture), `ocr-service` 40 (+8 skipped, unchanged),
  `rendering-service` 34, `search-service` 56 (+2 fixes: `_grant_root_read` needed an authorized
  principal for `POST /roles` since ADR 0071, an independent finding, see below), all green.
  `ruff check`/`ruff format --check` clean for all affected services.
- **Further regression finding, already caused by ADR 0071 (P19-S6)**: `search-service`'s
  `test_api.py::_grant_root_read` called `POST /roles` without `X-DMS-Principal` — broken since
  P19-S6's `PUT`/`POST /roles` gating. Fifth such finding after the four from ADR 0072 (case-service,
  auth-service, archival-service, reporting-service) — P19-S6 itself only tested permission-service/
  config-service/migration-service, so this fixture type was project-wide incompletely checked.
  Fixed with the same `_grant_role_admin_permission` session fixture pattern.
- **Fully verified live against the real running stack** (after rebuilding images for
  `virus-scan-service`/`ocr-service`/`rendering-service`/`search-service`/`archival-service` + restart,
  plus manually re-granting the running "everyone" role and restarting `permission-service` to seed the
  new `domain-admin-virus-scan` role): `GET /scans?status=infected` without header → `401`,
  authenticated without `admin.quarantine` → `403`, with the assigned `domain-admin-virus-scan` role →
  `200`; `GET /scans` (without `status` filter) still reachable ungated; `GET /config` (ocr-service)
  and `GET /renditions` (rendering-service) each without header → `401`, with principal → `200`. All
  container logs show clean starts without errors.
- **No resource tree entry for OCR results/renditions** (as with archival-service/reporting-service,
  ADR 0072) — `resource_id="root"` remains the project-wide uniform compromise for services without
  their own resource hierarchy.
