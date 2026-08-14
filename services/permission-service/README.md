# permission-service

RBAC with folder inheritance and a materialized, event-driven permission cache
(Concept 4.1) as well as scope locks (4.7, since P3-S4).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/roles` | Create a role (`name`, `description`, `permissions: [str]`) |
| `GET` | `/roles` | All roles |
| `POST` | `/role-assignments` | Assign a role to a principal on a resource |
| `DELETE` | `/role-assignments/{id}` | Remove an assignment |
| `GET` | `/resources/{id}` | Resource node (debug/test) |
| `PATCH` | `/resources/{id}` | Toggle inheritance on/off (`inherit: bool`) |
| `GET` | `/effective-permissions/{principal_id}/{resource_id}` | Effective roles/permissions (cached) |
| `GET` | `/check?principal_id=&resource_id=&permission=&access_type=read\|write` | Authorization check including scope locks |
| `POST` | `/scope-locks` | Set a scope lock (`resource_id`, `locked_by`, optional `reason`/`blocks_read`/`expires_at`) |
| `DELETE` | `/scope-locks/{id}` | Release a lock (`released_by`) |
| `GET` | `/scope-locks?resource_id=` | List locks |
| `GET` | `/scope-locks/effective/{resource_id}` | Active locks affecting this resource (including inherited) |
| `GET` | `/healthz` | Own health check |

## Scope locks (4.7)

Locks an entire resource subtree for regular users, independent of
RBAC — overlays the permission check rather than modifying it. Details, including the
`scope_lock.bypass` capability and auditing via `permission.scope_lock.*`
events: see `../../docs/services/permission-service.md`.

## Inheritance model

Standard DMS behavior (SharePoint/Alfresco-like): permissions inherit from
the root (`root`, automatically created at startup) downward. A
resource node with `inherit=false` breaks inheritance at that point
- its own assignments at that exact node still apply, only the further
climb to ancestors is skipped.

## Resource hierarchy: Folder Service (since P3-S3)

This service keeps its `resource_node` table in sync via structure events
published by the Folder Service (`folder.resource.created/.moved/.deleted`,
see `docs/services/permission-service.md` — contract verified live in P3-S3 against the
real Folder Service API, no adjustment needed). If this service starts
before any producer has created the `folder` stream (e.g. on the
very first startup of the whole stack), the subscription is skipped
(see `structure_consumer.py`) instead of blocking startup — a restart
after the Folder Service's first start catches up on it.

## Cache invalidation

Coarse-grained: every permission or structure change clears the entire
`effective_permission_cache` instead of just the affected subtree. Deliberate
simplification for the initial version - correct, but not maximally granular.

## Registry registration (since P4-S1)

Registers itself with the registry on startup via `dms-registry-client` (heartbeat, deregister on shutdown) - opt-in via `DMS_REGISTRY_SERVICE_BASE_URL`/`DMS_SELF_ADDRESS`, see `docs/services/gateway-service.md` for the consumer (API gateway, dynamic routing).

## Running locally

```bash
cd infra && docker compose up -d postgres nats permission-service
curl localhost:8004/healthz
```

## Tests

```bash
cd infra && docker compose up -d postgres nats && cd ..
uv run pytest services/permission-service/tests
```
