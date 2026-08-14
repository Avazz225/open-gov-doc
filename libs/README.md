# libs/

Shared Python packages used by multiple services — explicitly **not** shared business logic, only technical foundations (settings, DB setup, event-bus client, auth client). Each service remains functionally independent (Concept 3.1).

Created in P0-S2:

- `dms-common/` — settings, logging, OpenTelemetry base
- `dms-db-base/` — SQLAlchemy async setup, schema-per-service convention
- `dms-eventbus-client/` — publish/consume interface over NATS JetStream (swappable, Concept 3.4)
- `dms-auth-client/` — OIDC/JWT validation against Keycloak (Concept 4.4)
- `dms-constraint-engine/` — stateless object-type validation (Concept 2.2/4.5, since P3-S3, see [ADR 0003](../docs/adr/0003-constraint-engine-as-library.md))
- `dms-registry-client/` — self-registration of a service with the registry including heartbeat (Concept 3.2a, since P4-S1)
- `dms-permission-client/` — HTTP client against `permission-service` (RBAC checks, role assignment), consolidates the `PermissionServiceClient` class previously duplicated per service (Post-Roadmap Phase 19 Session 1)
- `dms-retry/` — shared backoff/jitter math (Full Jitter, AWS standard formula) for retry poll loops, **no** shared poll-loop framework (Post-Roadmap Phase 20 Session 1)
