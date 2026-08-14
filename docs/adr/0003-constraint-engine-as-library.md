# 0003 — Constraint engine as a shared library instead of a standalone service

**Status:** accepted
**Context:** Concept 2.2/4.5, Session P3-S3 (Object-Type Service, Folder Service, Constraint Engine)

## Decision

The roadmap names "Constraint Engine" as a standalone concept alongside the
Object-Type Service. It was **not** implemented as its own microservice, but
as a pure, stateless Python library (`libs/dms-constraint-engine`,
`validate(schema, name, attributes) -> list[str]`), embedded exclusively in
the **Object-Type Service**, which exposes it externally via its
`POST /object-types/{id}/validate` endpoint. Folder Service and Document
Service call this HTTP endpoint but do not import the library themselves.

## Rationale

The actual validation logic is a pure function with no state of its own, no
own persistence, no own events, and no need to be scaled or deployed
independently of the Object-Type Service - the only external input it needs
is the object type definition (which the Object-Type Service already
persists anyway). A dedicated microservice for just a stateless function
would only have produced additional network hops, health checks, its own
(empty) Postgres schema, and Compose entries, without offering an
architectural advantage (no independent scaling need, no independent
availability requirement).

The separation "lib" (logic) vs. "Object-Type Service" (persistence + API)
follows the same pattern as `dms-eventbus-client`/`dms-db-base`: shared code
lives in `libs/`, but is embedded in exactly one service context rather than
being imported directly across service boundaries. Document Service and
Folder Service exclusively talk to the Object-Type Service's HTTP API - no
import of another service's internals, consistent with the rest of the
architecture.

## Consequences

- An additional network hop per validation (Document/Folder Service →
  Object-Type Service) instead of an in-process call - uncritical for the
  current use case (validation at creation time, not a high-frequency hot
  path).
- Should the constraint engine later also be reused by the Workflow Engine
  (7.1) for BPMN gateway conditions, it can either be embedded again as a
  library (e.g. in a dedicated evaluation service) or addressed via the same
  `/validate`-style HTTP contract - both paths remain open, without this
  decision needing to be revisited.
- "References to other objects" (`type: "reference"`) are only checked by
  the library for format (non-empty string), not for actual existence at the
  referenced service - a generic "reference type → responsible service"
  resolution does not exist and would be a significantly larger extension
  than currently warranted (see `docs/services/object-type-service.md`).
