# 0005 — Gateway: registry-based routing, in-process rate limiting, centralized auth validation

**Status:** accepted
**Context:** Concept 3.5, Session P4-S1 (API gateway/BFF)

## Decision

1. The gateway resolves targets **dynamically via the registry**
   (`GET /instances/{service_type}`, cached briefly), instead of statically
   configuring backend addresses. For this, backend services actually
   register themselves with the registry for the first time — previously
   (since P1-S1) only the registry API itself existed, without a single
   consumer/producer. Self-registration is factored out into a new shared lib
   `dms-registry-client` (register-on-startup + periodic heartbeat +
   deregister-on-shutdown) and wired into seven services
   (auth/permission/storage/document/object-type/folder/audit-service).
2. Auth validation (JWT against Keycloak JWKS) runs **centrally in the
   gateway**, no longer potentially duplicated in every individual backend
   service. Successfully validated identity is passed to the downstream as
   `X-DMS-*` headers, but is not yet consumed by the backend services
   currently (see Consequences).
3. Rate limiting is a simple **in-process sliding-window counter** per client
   (no Redis or similar).

## Rationale

- **Registry instead of static configuration**: The concept explicitly
  requires "routing to backend services (using the registry)" (3.5). A
  static address list in the gateway would have ignored the registry as an
  already-existing but so-far-unused building block, and would have needed
  manual upkeep for every new service instance — contradicting the
  "plug-and-add" principle.
- **Self-registration as a shared lib instead of copying it into seven
  services**: register/heartbeat/deregister is pure boilerplate with no
  domain relevance to the respective service (analogous to
  `dms-eventbus-client`/`dms-auth-client`). Errors reaching the registry are
  logged but not re-raised — a backend service must not fail due to a
  briefly unreachable registry; discovery is an additional benefit, not a
  hard dependency.
- **Centralized instead of distributed auth validation**: Directly matches
  the BFF pattern from 3.5 ("central API gateway for authentication").
  Avoids every future new service having to bring its own JWKS validation
  logic.
- **In-process rate limiting instead of Redis**: For a single gateway
  instance (current state, no horizontal scaling planned), a shared external
  store is unnecessary complexity. The limitation is deliberately documented
  (see Consequences), not a hidden assumption.

## Consequences

- Backend services do not yet consume the identity headers passed on by the
  gateway (`X-DMS-Principal`/`X-DMS-Username`/`X-DMS-Roles`) — endpoints that
  need a principal (e.g. `permission-service`'s `/check`) continue to accept
  it explicitly as a parameter. Wiring this up follows once a UI/BFF session
  (P4-S2/S3) actually needs these headers; no break of existing interfaces,
  since these are additional, optional headers.
- Backend services themselves still do not validate bearer tokens (except
  for the Auth Service itself, which already validated `/me` before) — they
  implicitly trust that they are only reached via the gateway. In the
  current Docker Compose environment, their ports are nonetheless published
  directly to the outside (developer convenience); a real network perimeter
  (backend ports not public) is a later deployment/infrastructure step, not
  a code concern of this session.
- If the gateway scales horizontally, a client can bypass the rate limit
  across multiple instances (no shared counter) — can be switched to an
  external store (Redis) later if needed, without changing the
  `RateLimiter` interface itself (`allow(key) -> bool`).
- Instance selection among multiple healthy candidates is purely random
  load balancing, not least-connections/latency-aware routing — sufficient
  for the current development stage without real parallel scaling of a
  backend service type.
