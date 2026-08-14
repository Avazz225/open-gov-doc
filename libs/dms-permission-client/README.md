# dms-permission-client

HTTP client against `permission-service` (RBAC checks, role assignment) - consolidates the
`PermissionServiceClient` class that had previously been duplicated seven times per service (Post-Roadmap Phase 19 Session 1).

- `PermissionServiceClient(base_url, *, timeout=30.0, client=None)` - `client` allows injecting
  a preconfigured `httpx.AsyncClient` (e.g. with `httpx.MockTransport` for tests, see
  `libs/dms-metrics-client`'s `SensorConfigClient` for the same pattern).
  - `check(*, principal_id, resource_id, permission, access_type="read") -> bool` - single check
    against `GET /check`.
  - `check_batch(*, principal_id, permission, access_type="read", resource_ids) -> dict[str, bool]` -
    batch check against `POST /check/batch`, empty result with no server call for an empty list.
  - `has_permission(principal_id, permission) -> bool` - domain-admin gate against
    `GET /effective-permissions/{principal_id}/root`.
  - `ensure_role_assignment(*, principal_id, role_name, resource_id="root") -> None` - idempotent,
    raises `RoleNotFoundError` for an unknown role, or `RoleAssignmentPendingApprovalError` if
    the installation has `permission.role_assignment.create` configured to require four-eyes approval
    (ADR 0060) and the assignment is therefore not yet in effect.
  - `get_role_id(name) -> int | None`, `close()`.

**Deliberately no forced refactor of the existing duplicates**: `document-service`, `search-service`,
`workflow-service`, `config-service`, `license-service`, `monitoring-service`,
`plugin-orchestration-service`, `query-service`, `teamspace-service`, and `auth-service` retain
their own, in some cases service-specifically extended, `PermissionServiceClient` class for now (e.g.
`workflow-service`'s `check_delegation`, `query-service`'s four-eyes endpoints,
`teamspace-service`'s role bootstrap) - a pure migration with no functional benefit was deliberately
not undertaken. New consumers (Phase 19 from Session 2 onward) use this package directly.

## Usage

```python
from dms_permission_client import PermissionServiceClient

client = PermissionServiceClient(settings.permission_service_base_url)
allowed = await client.check(principal_id=user_id, resource_id=folder_id, permission="folder.read")
```

## Tests

Purely at the unit level with `httpx.MockTransport` (no real `permission-service` needed):

```bash
uv run pytest libs/dms-permission-client/tests
```
