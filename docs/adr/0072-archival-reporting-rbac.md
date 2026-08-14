# 0072 — Archival/reporting RBAC (archival-service, reporting-service)

**Status:** accepted (session 7 of 11, see phase 19 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap phase 19 session 7, affects `archival-service`, `reporting-service`,
`permission-service`

## Decision

`archival-service` (except for the separate, narrower `archive_retrieval_role` gate for
retrieval/disposal access scope/package download) and `reporting-service` (incl. forensic trace) had
previously **NO** general permission checking at all. This session closes both gaps:

1. **`archival-service`**: new `_require_archival_permission(x_dms_principal, *, access_type)` helper
   (`main.py`, first consumer of `libs/dms-permission-client` in this service) — `401` without
   `X-DMS-Principal`, otherwise `PermissionServiceClient.check(..., permission="archival.read"|
   "archival.write", resource_id=ROOT)`, `403` on denial. Applies to ALL eight endpoints
   (`GET/POST /archival-transfers*`, `GET /released-items`, `GET/POST .../case-archival-transfers*`).
   The existing `archive_retrieval_role` gate (X-DMS-Roles, concept 5.6 "decryption only for
   authorized roles") remains **unchanged and additional** — both checks run
   sequentially, neither replaces the other.
2. **`reporting-service`**: new `_require_reporting_permission(x_dms_principal, *, permission,
   access_type)` helper. Standard reports/schedules/downloads use `reporting.read`/`reporting.write`;
   the forensic trace (`GET /forensic-trace`, `GET /forensic-trace/export`) gets the separate, narrower
   `reporting.forensic_trace` instead of `reporting.read` — a dedicated permission due to its
   increased sensitivity (potentially comprehensive user activity disclosure, see
   docs/services/reporting-service.md "Open Points").
3. **`queried_by` spoofing gap in the forensic trace closed**: the previously freely client-chosen
   `queried_by` query parameter is removed without replacement — self-auditing
   (`_record_trace_query`, `reporting.forensic_trace.queried` event) now uses exclusively the
   verified `X-DMS-Principal` header as the actor source.
4. **"everyone" group (ADR 0067) extended with five new permissions**: `archival.read`,
   `archival.write`, `reporting.read`, `reporting.write`, `reporting.forensic_trace` — preserves the
   previous de-facto-open behavior of both services, but makes it admin-editable. As
   documented in P19-S5/P19-S6, `ensure_everyone_role` is not self-healing — the already running
   installation was caught up once manually via `PUT /roles/{id}`.

## Rationale

- **Why `archival.read`/`.write` and `reporting.read`/`.write` instead of a new domain admin role**:
  both services are (like case-service) not pure admin domains in the sense of 4.6, but
  business functions with read/write access — `<domain>.read`/`<domain>.write` is the
  established naming pattern (`case.read`/`.write`, `document.read`/`.write`), matching `GET /check`'s
  `access_type` parameter.
- **Why `reporting.forensic_trace` as its own, third permission instead of `reporting.read`**: the
  forensic trace can potentially disclose sensitive user activity system-wide (5.4b) — a separate
  permission allows an administrator to grant normal reports more broadly than the forensic trace,
  without necessarily coupling both. Same granularity principle as `admin.monitoring` vs.
  `admin.object_config` (separate admin domains for separate sensitivity levels).
- **Why `archive_retrieval_role` is NOT replaced by the new RBAC check**: it is an
  independent mechanism explicitly anchored in the concept ("decryption only for authorized
  roles", 5.6) with its own semantics (who may see decrypted content) — replacing it would have
  weakened an existing, concept-anchored control instead of complementing it. Layering instead of
  replacing is also the pattern already established in ADR 0070.
- **Why `queried_by` was removed instead of just additionally verified**: a field still freely
  chosen by the client would, despite the new authentication, have continued to allow a
  misleading, unverified second "actor" in the audit trail — the only reliable actor source after
  this session is the header, an additional, potentially contradictory field would have been
  pure confusion with no security value.

## A pre-existing, previously undiscovered test regression problem caused by P19-S6, found and fixed

`permission-service`'s `PUT /roles/{id}` gating (P19-S6, ADR 0071) broke **four existing
`everyone_role_without` test fixtures** (`archival-service` [new in this session], `reporting-service`
[new], `case-service`, `auth-service`), which call this endpoint without an `X-DMS-Principal`
header — a regression risk that went undiscovered in P19-S6 itself, since only
`permission-service`/`config-service`/`migration-service` were tested there, not the services with this
fixture. Fixed:

- **`case-service`**: new test principal `ROLE_ADMIN_PRINCIPAL_ID`, to which a new
  `_grant_role_admin_permission` session fixture (analogous to `_grant_config_admin_permission`)
  assigns the `domain-admin-users` role.
- **`auth-service`**: more complex, since `permission-service`'s `principal_id` for the
  technical `users-admin` account there is `TechnicalAccount.id` (an integer as a string), not the
  username (see `main.py`'s `ensure_role_assignment(principal_id=account_id, ...)`) — the fixture now
  resolves this ID via its **own** SQLAlchemy engine (not `app.state.session_factory`, which is
  bound to the TestClient's internal event loop and would have failed with "attached
  to a different loop" from a separate `asyncio.run()` fixture — the same asyncpg/pytest-asyncio
  problem already documented elsewhere in the project, cf. `reporting-service/tests/test_api.py::poll_env`).
- **`archival-service`/`reporting-service`** (this session): same pattern as `case-service` — each
  gets its own `ROLE_ADMIN_PRINCIPAL_ID` with a `domain-admin-users` assignment.

## Consequences

- **Tests**: `archival-service` 61 (previously 55, +6: 401/403 tests, `everyone_role_without`
  fixture including the role grant), `reporting-service` 57 (previously 51, +6, incl. adjusted
  forensic trace tests after removing `queried_by`), `auth-service` 96 (unchanged in test count, but
  the three tests previously broken by P19-S6 are green again), `case-service` 50 (unchanged in test
  count, same pattern). `ruff check`/`ruff format --check` clean for all four services.
- **Fully verified live against the real running stack** (after image rebuild of
  `archival-service`/`reporting-service` + restart, plus a manual `PUT /roles/{id}` of the already
  running "everyone" role): `GET /archival-transfers` without a header → `401`, with a principal → `200`;
  `GET /reports/document-volume` without a header → `401`, with a principal → `200`; `GET /forensic-trace`
  without a header → `401`, with a principal → `200`. Both container logs show clean starts with no
  errors.
- **No other service calls archival-service/reporting-service via HTTP** (confirmed via
  research) — unlike case-service (P19-S5), no `system:<Service>` header fix was needed
  elsewhere.
- **`GET /archival-transfers/due-for-archival` does not exist on archival-service** (unlike
  case-service) — all eight endpoints in this session are human-usable admin views, no
  pure machine-to-machine callbacks were left ungated.
