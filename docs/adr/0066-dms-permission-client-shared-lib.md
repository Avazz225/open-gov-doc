# 0066 — Shared `dms-permission-client` library instead of further duplicates

**Status:** accepted (session 1 of 11, see phase 19 in `IMPLEMENTATION_PLAN.md`)
**Context:** Post-roadmap phase 19 session 1, affects `libs/dms-permission-client` (new)

## Decision

New shared package `libs/dms-permission-client` (uv workspace member via the existing
`members = ["libs/*", "services/*", "tools/*"]` glob, no manual entry needed), which bundles the four
operations shared across the ten `PermissionServiceClient` duplicates already present in the project:

- `check(*, principal_id, resource_id, permission, access_type="read") -> bool` — generalizes
  `document-service`'s previous `check_read`/`check_write` pair via the `access_type` parameter.
- `check_batch(*, principal_id, permission, access_type="read", resource_ids) -> dict[str, bool]` —
  taken 1:1 from `search-service`/`query-service` (identical implementation in both).
- `has_permission(principal_id, permission) -> bool` — domain admin root gate, present
  byte-identically in 7 of the 10 existing duplicates.
- `ensure_role_assignment(*, principal_id, role_name, resource_id="root") -> None` — taken from
  `auth-service`'s implementation, including its `RoleAssignmentPendingApprovalError`
  check (ADR 0060, four-eyes principle on `permission.role_assignment.create`) and `RoleNotFoundError`.
  Extended with `resource_id` (default still `"root"`), so future consumers such as
  `teamspace-service`'s resource-scoped assignments can use the same method without maintaining
  their own copy.

Structure/conventions taken 1:1 from `libs/dms-auth-client`/`libs/dms-metrics-client`: hatchling
build, `[tool.uv.sources] dms-permission-client = { workspace = true }` in the consuming services'
`pyproject.toml`, `client: httpx.AsyncClient | None = None` constructor parameter for testability via
`httpx.MockTransport` (pattern taken from `dms-metrics-client`'s `SensorConfigClient`, since this
project uses neither `respx` nor `pytest-httpx`).

## Rationale

- **Why now and not earlier**: the duplication had been known since P6-S5, but was only explicitly
  named for consolidation with the "Open Points" triage - previously there was no occasion, since
  each copy grew organically for its respective use case (domain admin gate, search filtering,
  share link check, ...) independently.
- **Why NO forced migration of the 10 existing duplicates**: a plain switchover without a
  functional change would be refactoring for its own sake (violates the project convention, see
  `CLAUDE.md`/`CONTRIBUTING.md`) and carries unnecessary regression risk for already working,
  mostly untested code (see "known limitation" below). Five of the ten duplicates also have
  service-specific extra methods (`workflow-service`'s `check_delegation`, `query-service`'s
  four-eyes endpoints, `teamspace-service`'s role bootstrap, `config-service`'s role/approval
  management), which are deliberately NOT part of the shared library - each covers only one
  service, and including them in the shared library would unnecessarily bloat its surface.
  Migration candidates for later, standalone sessions (not part of this decision).
- **Why `resource_id` as a parameter instead of hardcoded `"root"`**: `auth-service`'s original only
  knew root-resource assignments (superuser/domain admins). `teamspace-service`'s
  `grant_resource_access`/`revoke_resource_access` assign the same operation but to a concrete
  teamspace root folder resource - an optional parameter with a sensible default covers both cases
  without maintaining two methods.
- **Why `httpx.MockTransport` instead of `respx`/`pytest-httpx`**: neither library is
  already a project dependency; `httpx.MockTransport` is part of `httpx` itself (already present
  everywhere) and fully covers the need (request inspection + controlled responses) -
  confirmed by the already established, identical pattern in `libs/dms-metrics-client`'s
  `SensorConfigClient` tests and several service tests (`workflow-service/tests/test_license_client.py`
  among others).

## Consequences

- **First real test coverage of a `PermissionServiceClient`-style HTTP client in this project**:
  none of the 10 existing duplicates (not even `document-service`'s already production-used
  `check_read`/`check_write`) previously had its own unit tests - 12 new tests cover all four methods of
  the shared library (including the idempotency path, pending-approval path, empty
  `resource_ids` short-circuit).
- **`uv.lock` updated** (`uv lock`), new package added as the 152nd workspace member. No
  Dockerfile of an existing service needed to change - `COPY libs/ libs/` picks up the new
  directory automatically as soon as a service declares it as a dependency in the future.
- **No service uses the library in this session** - a pure infrastructure session, analogous to
  P18-S1. The first actual consumer follows with the upcoming phase 19 sessions (gating new
  endpoints); existing duplicates remain unchanged.
- **Known limitation**: `workflow-service`'s `check_delegation`, `query-service`'s four-eyes client
  methods, `teamspace-service`'s role bootstrap, and `config-service`'s role/approval management
  remain duplicated / service-owned - no consolidation planned in this session.
